package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestRedactFindsSecretsAndPreservesContext(t *testing.T) {
	input := "User jane@example.com cannot VPN from 10.0.4.15 with password=hunter22 and Bearer abcdefghijklmnop."
	sanitized, findings := redact(input)

	if len(findings) != 4 {
		t.Fatalf("expected 4 findings, got %d: %#v", len(findings), findings)
	}
	for _, leaked := range []string{"jane@example.com", "10.0.4.15", "hunter22", "abcdefghijklmnop"} {
		if strings.Contains(sanitized, leaked) {
			t.Fatalf("sanitized text leaked %q: %s", leaked, sanitized)
		}
	}
	for _, required := range []string{"[EMAIL_1]", "[IP_ADDRESS_1]", "[CREDENTIAL_1]", "[BEARER_TOKEN_1]"} {
		if !strings.Contains(sanitized, required) {
			t.Fatalf("sanitized text missing %q: %s", required, sanitized)
		}
	}
}

func TestComposeRawTextPrefersRawText(t *testing.T) {
	got := composeRawText(ingestRequest{
		RawText:          "raw",
		ShortDescription: "short",
		Description:      "desc",
	})
	if got != "raw" {
		t.Fatalf("expected raw text, got %q", got)
	}
}

func TestRedactWithoutFindingsSerializesEmptyArray(t *testing.T) {
	sanitized, findings := redact("Equipment selection dropdown is not saving")
	if sanitized != "Equipment selection dropdown is not saving" {
		t.Fatalf("expected unchanged text, got %q", sanitized)
	}
	if findings == nil || len(findings) != 0 {
		t.Fatalf("expected non-nil empty findings slice, got %#v", findings)
	}
	payload, err := json.Marshal(ingestResponse{Findings: findings})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(payload), `"findings":[]`) {
		t.Fatalf("expected findings to serialize as [], got %s", payload)
	}
}

func TestIngestRejectsUnknownFields(t *testing.T) {
	a := &app{}
	req := httptest.NewRequest(
		http.MethodPost,
		"/v1/ingest/stream",
		strings.NewReader(`{"short_description":"VPN down","description":"cannot connect","unexpected":true}`),
	)
	w := httptest.NewRecorder()

	a.ingest(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for unknown field, got %d", w.Code)
	}
}

func TestIngestRejectsInvalidPriorityBounds(t *testing.T) {
	a := &app{}
	req := httptest.NewRequest(
		http.MethodPost,
		"/v1/ingest/stream",
		strings.NewReader(`{"short_description":"VPN down","description":"cannot connect","urgency":9}`),
	)
	w := httptest.NewRecorder()

	a.ingest(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid urgency, got %d", w.Code)
	}
}
