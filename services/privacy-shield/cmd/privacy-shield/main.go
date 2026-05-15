package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"sort"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

const (
	detectorVersion = "privacy-shield-regex-v1"
	policyVersion   = "enterprise-ticket-policy-v1"
	maxBodyBytes    = 1 << 20
)

type ingestRequest struct {
	TicketID         string         `json:"ticket_id"`
	Number           string         `json:"number"`
	ShortDescription string         `json:"short_description"`
	Description      string         `json:"description"`
	Urgency          int            `json:"urgency"`
	Impact           int            `json:"impact"`
	Category         string         `json:"category"`
	AssignmentGroup  string         `json:"assignment_group"`
	Resolution       string         `json:"resolution"`
	RawText          string         `json:"raw_text"`
	Metadata         map[string]any `json:"metadata"`
	BypassRedpanda   bool           `json:"bypass_redpanda"`
	ReturnSanitized  bool           `json:"return_sanitized"`
	Source           string         `json:"source"`
}

type finding struct {
	EntityType  string  `json:"entity_type"`
	Placeholder string  `json:"placeholder"`
	Confidence  float32 `json:"confidence"`
	StartOffset uint32  `json:"start_offset"`
	EndOffset   uint32  `json:"end_offset"`
}

type ingestResponse struct {
	StreamID        string    `json:"stream_id"`
	TicketID        string    `json:"ticket_id"`
	RawSHA256       string    `json:"raw_sha256"`
	SanitizedSHA256 string    `json:"sanitized_sha256"`
	SanitizedText   string    `json:"sanitized_text,omitempty"`
	Findings        []finding `json:"findings"`
	DetectorVersion string    `json:"detector_version"`
	PolicyVersion   string    `json:"policy_version"`
	Published       bool      `json:"published"`
}

type streamEvent struct {
	ingestRequest
	StreamID        string    `json:"stream_id"`
	RawSHA256       string    `json:"raw_sha256"`
	SanitizedSHA256 string    `json:"sanitized_sha256"`
	SanitizedText   string    `json:"sanitized_text"`
	Findings        []finding `json:"findings"`
	DetectorVersion string    `json:"detector_version"`
	PolicyVersion   string    `json:"policy_version"`
	CreatedAt       string    `json:"created_at"`
}

type detector struct {
	entityType string
	confidence float32
	re         *regexp.Regexp
}

type match struct {
	start      int
	end        int
	entityType string
	confidence float32
}

type app struct {
	kafka     *kgo.Client
	log       *slog.Logger
	processed atomic.Uint64
	redacted  atomic.Uint64
	errors    atomic.Uint64
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	kafka, err := newKafkaClient()
	if err != nil {
		logger.Warn("redpanda disabled", "error", err)
	}
	if kafka != nil {
		defer kafka.Close()
	}

	a := &app{kafka: kafka, log: logger}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", a.health)
	mux.HandleFunc("GET /metrics", a.metrics)
	mux.HandleFunc("POST /v1/ingest/stream", a.ingest)

	addr := env("HTTP_ADDR", ":8080")
	server := &http.Server{
		Addr:              addr,
		Handler:           requestLogger(logger, mux),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    1 << 20,
	}

	go func() {
		logger.Info("privacy shield listening", "addr", addr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("server stopped", "error", err)
			stop()
		}
	}()

	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		logger.Error("shutdown failed", "error", err)
	}
}

func newKafkaClient() (*kgo.Client, error) {
	rawBrokers := strings.Split(env("REDPANDA_BROKERS", ""), ",")
	brokers := make([]string, 0, len(rawBrokers))
	for _, broker := range rawBrokers {
		if trimmed := strings.TrimSpace(broker); trimmed != "" {
			brokers = append(brokers, trimmed)
		}
	}
	if len(brokers) == 0 {
		return nil, errors.New("REDPANDA_BROKERS is empty")
	}
	opts := []kgo.Opt{
		kgo.SeedBrokers(brokers...),
		kgo.RequiredAcks(kgo.AllISRAcks()),
		kgo.AllowAutoTopicCreation(),
		kgo.ProducerBatchCompression(kgo.SnappyCompression()),
	}
	return kgo.NewClient(opts...)
}

func (a *app) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":           "ok",
		"detector_version": detectorVersion,
		"policy_version":   policyVersion,
		"redpanda_enabled": a.kafka != nil,
	})
}

func (a *app) metrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	fmt.Fprintf(w, "privacy_shield_processed_total %d\n", a.processed.Load())
	fmt.Fprintf(w, "privacy_shield_redacted_entities_total %d\n", a.redacted.Load())
	fmt.Fprintf(w, "privacy_shield_errors_total %d\n", a.errors.Load())
}

func (a *app) ingest(w http.ResponseWriter, r *http.Request) {
	var req ingestRequest
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		a.errors.Add(1)
		writeError(w, http.StatusBadRequest, "invalid JSON payload")
		return
	}
	if (req.Urgency != 0 && (req.Urgency < 1 || req.Urgency > 3)) ||
		(req.Impact != 0 && (req.Impact < 1 || req.Impact > 3)) {
		a.errors.Add(1)
		writeError(w, http.StatusBadRequest, "urgency and impact must be between 1 and 3 when provided")
		return
	}

	rawText := composeRawText(req)
	if strings.TrimSpace(rawText) == "" {
		a.errors.Add(1)
		writeError(w, http.StatusBadRequest, "ticket text is required")
		return
	}

	streamID := stableID("stream", rawText, time.Now().UTC().Format(time.RFC3339Nano))
	ticketID := req.TicketID
	if ticketID == "" {
		ticketID = stableID("ticket", rawText, req.Number)
	}

	sanitized, findings := redact(rawText)
	rawHash := sha256Hex(rawText)
	sanitizedHash := sha256Hex(sanitized)
	a.processed.Add(1)
	a.redacted.Add(uint64(len(findings)))

	event := streamEvent{
		ingestRequest:   req,
		StreamID:        streamID,
		RawSHA256:       rawHash,
		SanitizedSHA256: sanitizedHash,
		SanitizedText:   sanitized,
		Findings:        findings,
		DetectorVersion: detectorVersion,
		PolicyVersion:   policyVersion,
		CreatedAt:       time.Now().UTC().Format(time.RFC3339Nano),
	}
	event.TicketID = ticketID

	published := false
	if a.kafka != nil && !req.BypassRedpanda {
		if err := a.publish(r.Context(), "tickets.sanitized", ticketID, event); err != nil {
			a.errors.Add(1)
			a.log.Error("publish sanitized event failed", "error", err)
			writeError(w, http.StatusBadGateway, "failed to publish sanitized event")
			return
		}
		if len(findings) > 0 {
			if err := a.publish(r.Context(), "privacy.audit", streamID, event); err != nil {
				a.errors.Add(1)
				a.log.Error("publish audit event failed", "error", err)
				writeError(w, http.StatusBadGateway, "failed to publish audit event")
				return
			}
		}
		published = true
	}

	resp := ingestResponse{
		StreamID:        streamID,
		TicketID:        ticketID,
		RawSHA256:       rawHash,
		SanitizedSHA256: sanitizedHash,
		Findings:        findings,
		DetectorVersion: detectorVersion,
		PolicyVersion:   policyVersion,
		Published:       published,
	}
	if req.ReturnSanitized || req.BypassRedpanda {
		resp.SanitizedText = sanitized
	}
	writeJSON(w, http.StatusAccepted, resp)
}

func (a *app) publish(ctx context.Context, topic, key string, value any) error {
	payload, err := json.Marshal(value)
	if err != nil {
		return err
	}
	results := a.kafka.ProduceSync(ctx, &kgo.Record{
		Topic: topic,
		Key:   []byte(key),
		Value: payload,
	})
	return results.FirstErr()
}

func composeRawText(req ingestRequest) string {
	if strings.TrimSpace(req.RawText) != "" {
		return req.RawText
	}
	parts := []string{req.ShortDescription, req.Description}
	return strings.TrimSpace(strings.Join(parts, "\n\n"))
}

var detectors = []detector{
	{"EMAIL", 0.99, regexp.MustCompile(`(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b`)},
	{"IP_ADDRESS", 0.95, regexp.MustCompile(`\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b`)},
	{"CREDENTIAL", 0.97, regexp.MustCompile(`(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*["']?[^"'\s,;]{6,}`)},
	{"BEARER_TOKEN", 0.98, regexp.MustCompile(`(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{12,}`)},
	{"AWS_ACCESS_KEY", 0.99, regexp.MustCompile(`\b(?:AKIA|ASIA)[A-Z0-9]{16}\b`)},
	{"PRIVATE_KEY", 0.99, regexp.MustCompile(`-----BEGIN [A-Z ]*PRIVATE KEY-----`)},
	{"PHONE_NUMBER", 0.85, regexp.MustCompile(`(?i)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b`)},
	{"ACCOUNT_ID", 0.80, regexp.MustCompile(`(?i)\b(?:(?:account|acct|merchant|customer)[_-]?(?:id)?|user[_-]?id)\s*[:#=]\s*[A-Z0-9\-]{4,}\b`)},
}

func redact(input string) (string, []finding) {
	matches := findMatches(input)
	if len(matches) == 0 {
		return input, []finding{}
	}

	counts := map[string]int{}
	var findings []finding
	var out strings.Builder
	last := 0
	for _, m := range matches {
		if m.start < last {
			continue
		}
		counts[m.entityType]++
		placeholder := fmt.Sprintf("[%s_%d]", m.entityType, counts[m.entityType])
		out.WriteString(input[last:m.start])
		out.WriteString(placeholder)
		findings = append(findings, finding{
			EntityType:  m.entityType,
			Placeholder: placeholder,
			Confidence:  m.confidence,
			StartOffset: uint32(m.start),
			EndOffset:   uint32(m.end),
		})
		last = m.end
	}
	out.WriteString(input[last:])
	return out.String(), findings
}

func findMatches(input string) []match {
	var matches []match
	for _, d := range detectors {
		for _, loc := range d.re.FindAllStringIndex(input, -1) {
			matches = append(matches, match{
				start:      loc[0],
				end:        loc[1],
				entityType: d.entityType,
				confidence: d.confidence,
			})
		}
	}
	sort.SliceStable(matches, func(i, j int) bool {
		if matches[i].start == matches[j].start {
			return matches[i].end-matches[i].start > matches[j].end-matches[j].start
		}
		return matches[i].start < matches[j].start
	})
	return matches
}

func stableID(prefix string, values ...string) string {
	h := sha256.New()
	for _, v := range values {
		h.Write([]byte(v))
		h.Write([]byte{0})
	}
	return fmt.Sprintf("%s_%s", prefix, hex.EncodeToString(h.Sum(nil))[:16])
}

func sha256Hex(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

func env(name, fallback string) string {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	return value
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	secureHeaders(w)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]any{"error": message})
}

func requestLogger(logger *slog.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		secureHeaders(w)
		next.ServeHTTP(w, r)
		logger.Info("request", "method", r.Method, "path", r.URL.Path, "duration_ms", time.Since(start).Milliseconds())
	})
}

func secureHeaders(w http.ResponseWriter) {
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Cache-Control", "no-store")
}
