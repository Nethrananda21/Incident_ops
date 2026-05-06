package main

import (
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
