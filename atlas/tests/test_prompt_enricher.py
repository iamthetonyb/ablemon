"""Tests for the PromptEnricher module."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.routing.prompt_enricher import PromptEnricher, enrich_prompt


def test_basic_enrichment():
    """Flavor words get expanded with domain-specific criteria."""
    enricher = PromptEnricher()

    # Code domain + "robust" (auth keywords push toward security, which is correct)
    result = enricher.enrich("Write a robust Python function to parse CSV files")
    assert result.enrichment_level != "none"
    assert "robust" in result.flavor_words_found
    assert "error handling" in result.enriched.lower() or "validation" in result.enriched.lower()
    assert result.domain == "code"
    print(f"  [PASS] Code + robust: level={result.enrichment_level}, domain={result.domain}")

    # Content domain + "professional" (video = multimedia content)
    result = enricher.enrich("Shoot a professional YouTube video about cooking techniques")
    assert result.enrichment_level != "none"
    assert "professional" in result.flavor_words_found
    enriched_lower = result.enriched.lower()
    assert any(kw in enriched_lower for kw in ["color grading", "aspect ratio", "audio", "branding"])
    assert result.domain == "content"
    print(f"  [PASS] Content + professional: level={result.enrichment_level}, domain={result.domain}")

    # Security domain + "thorough"
    result = enricher.enrich("Do a thorough security audit of our authentication system")
    assert result.enrichment_level != "none"
    assert "thorough" in result.flavor_words_found
    assert "owasp" in result.enriched.lower()
    assert result.domain == "security"
    print(f"  [PASS] Security + thorough: level={result.enrichment_level}, domain={result.domain}")


def test_multiple_flavor_words():
    """Multiple flavor words in one prompt all get expanded."""
    enricher = PromptEnricher()

    result = enricher.enrich("Build a robust and scalable microservice with clean code")
    assert len(result.flavor_words_found) >= 2
    assert "robust" in result.flavor_words_found
    assert result.enrichment_level in ("standard", "deep")
    print(f"  [PASS] Multi-word: found={result.flavor_words_found}, level={result.enrichment_level}")


def test_skip_patterns():
    """Simple messages bypass enrichment."""
    enricher = PromptEnricher()

    # Greeting
    result = enricher.enrich("Hello")
    assert result.enrichment_level == "none"
    assert result.skip_reason is not None
    print(f"  [PASS] Skip greeting: reason={result.skip_reason}")

    # Slash command
    result = enricher.enrich("/status")
    assert result.enrichment_level == "none"
    print(f"  [PASS] Skip slash command")

    # Too short
    result = enricher.enrich("yes")
    assert result.enrichment_level == "none"
    print(f"  [PASS] Skip too short")

    # Already detailed (long message with no flavor words)
    long_msg = "Please fix the bug in line 42 of server.py where the connection pool exhausts"
    result = enricher.enrich(long_msg)
    assert result.enrichment_level == "none"
    assert result.skip_reason == "no flavor words detected"
    print(f"  [PASS] Skip no flavor words: reason={result.skip_reason}")


def test_domain_detection():
    """Domain detection picks the right domain."""
    enricher = PromptEnricher()

    cases = [
        ("Write a blog post about AI", "copywriting"),  # Blog = written content → copywriting
        ("Shoot a YouTube video about cooking", "content"),  # Video = multimedia → content
        ("Build a REST API with FastAPI", "code"),
        ("Audit our OAuth implementation", "security"),
        ("Design a dashboard for analytics", "design"),
        ("Create an email campaign for our launch", "copywriting"),
        ("Research competitor pricing strategies", "research"),
    ]

    for msg, expected_domain in cases:
        result = enricher.enrich(f"Create a robust {msg}")
        assert result.domain == expected_domain, f"Expected {expected_domain}, got {result.domain} for: {msg}"
        print(f"  [PASS] Domain: '{msg[:40]}...' → {result.domain}")


def test_preserves_original():
    """Enrichment adds to, never removes from, the original message."""
    enricher = PromptEnricher()

    original = "Write a robust login system with JWT"
    result = enricher.enrich(original)
    assert original in result.enriched
    assert len(result.enriched) > len(original)
    print(f"  [PASS] Original preserved, +{len(result.enriched) - len(original)} chars added")


def test_no_over_enrichment():
    """Already specific messages don't get double-enriched."""
    enricher = PromptEnricher()

    # Very long, specific message — should skip (over MAX_ENRICH_LENGTH)
    specific = "x " * 1001  # 2002 chars
    result = enricher.enrich(specific)
    assert result.enrichment_level == "none"
    assert result.skip_reason == "already detailed enough"
    print(f"  [PASS] No over-enrichment for long messages")


def test_design_domain():
    """Design domain gets UI/UX specific criteria."""
    enricher = PromptEnricher()

    result = enricher.enrich("Create an elegant dashboard with modern UI components")
    assert result.domain == "design"
    assert "elegant" in result.flavor_words_found or "modern" in result.flavor_words_found
    # Should mention design-specific things like spacing, accessibility, etc.
    enriched_lower = result.enriched.lower()
    assert any(kw in enriched_lower for kw in ["spacing", "wcag", "transition", "dark mode", "micro-interaction"])
    print(f"  [PASS] Design domain: {result.flavor_words_found}")


def test_copywriting_domain():
    """Copywriting domain gets conversion-specific criteria."""
    enricher = PromptEnricher()

    result = enricher.enrich("Write a robust email pitch for our SaaS product launch")
    assert result.domain == "copywriting"
    enriched_lower = result.enriched.lower()
    assert any(kw in enriched_lower for kw in ["aida", "pas", "cta", "framework", "meta-program"])
    print(f"  [PASS] Copywriting domain: criteria includes frameworks")


def test_enrichment_convenience_function():
    """The one-liner convenience function works."""
    result = enrich_prompt("Build a robust REST API")
    assert result.enrichment_level != "none"
    assert result.domain == "code"
    print(f"  [PASS] Convenience function works")


if __name__ == "__main__":
    print("=" * 60)
    print("PromptEnricher Tests")
    print("=" * 60)

    tests = [
        test_basic_enrichment,
        test_multiple_flavor_words,
        test_skip_patterns,
        test_domain_detection,
        test_preserves_original,
        test_no_over_enrichment,
        test_design_domain,
        test_copywriting_domain,
        test_enrichment_convenience_function,
    ]

    passed = 0
    failed = 0
    for test in tests:
        print(f"\n{test.__name__}:")
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")
