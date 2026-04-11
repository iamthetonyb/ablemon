"""Tests for A7 — Plugin Security Hardening.

Covers: symlink blocking, file extension allowlist, SHA-256 hash verification.
"""

import pytest
import hashlib
from pathlib import Path

from able.security.malware_scanner import (
    MalwareScanner,
    ThreatCategory,
    ThreatLevel,
)


@pytest.fixture
def scanner():
    return MalwareScanner()


@pytest.fixture
def skill_dir(tmp_path):
    """Create a clean skill directory."""
    skill = tmp_path / "test_skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Test Skill\nA test skill.")
    (skill / "implement.py").write_text("def run(): return 'ok'\n")
    (skill / "config.yaml").write_text("name: test\n")
    return skill


# ── Symlink blocking ─────────────────────────────────────────────

class TestSymlinkBlocking:

    def test_no_symlinks_clean(self, skill_dir):
        threats = MalwareScanner.check_symlinks(skill_dir)
        assert threats == []

    def test_symlink_detected(self, skill_dir):
        # Create a symlink to /etc/passwd
        link = skill_dir / "sneaky_link"
        link.symlink_to("/etc/hosts")
        threats = MalwareScanner.check_symlinks(skill_dir)
        assert len(threats) == 1
        assert threats[0].category == ThreatCategory.PRIVILEGE_ESCALATION
        assert threats[0].level == ThreatLevel.DANGEROUS
        assert "symlink" in threats[0].description.lower()

    def test_nested_symlink_detected(self, skill_dir):
        subdir = skill_dir / "lib"
        subdir.mkdir()
        link = subdir / "escape"
        link.symlink_to("/tmp")
        threats = MalwareScanner.check_symlinks(skill_dir)
        assert len(threats) == 1


# ── File extension allowlist ──────────────────────────────────────

class TestFileExtensionAllowlist:

    def test_allowed_extensions_clean(self, skill_dir):
        threats = MalwareScanner.check_file_extensions(skill_dir)
        assert threats == []

    def test_binary_extension_flagged(self, skill_dir):
        (skill_dir / "payload.exe").write_bytes(b"\x00" * 10)
        threats = MalwareScanner.check_file_extensions(skill_dir)
        assert len(threats) == 1
        assert ".exe" in threats[0].description

    def test_so_extension_flagged(self, skill_dir):
        (skill_dir / "evil.so").write_bytes(b"\x00" * 10)
        threats = MalwareScanner.check_file_extensions(skill_dir)
        assert any(".so" in t.description for t in threats)

    def test_dll_extension_flagged(self, skill_dir):
        (skill_dir / "inject.dll").write_bytes(b"\x00" * 10)
        threats = MalwareScanner.check_file_extensions(skill_dir)
        assert len(threats) >= 1

    def test_py_yaml_md_json_allowed(self, skill_dir):
        (skill_dir / "util.py").write_text("pass")
        (skill_dir / "config.yml").write_text("x: 1")
        (skill_dir / "README.md").write_text("# hi")
        (skill_dir / "schema.json").write_text("{}")
        threats = MalwareScanner.check_file_extensions(skill_dir)
        assert threats == []

    def test_txt_toml_cfg_allowed(self, skill_dir):
        (skill_dir / "notes.txt").write_text("note")
        (skill_dir / "pyproject.toml").write_text("[tool]")
        (skill_dir / "setup.cfg").write_text("[options]")
        threats = MalwareScanner.check_file_extensions(skill_dir)
        assert threats == []


# ── SHA-256 hash verification ─────────────────────────────────────

class TestHashVerification:

    def test_correct_hash_passes(self, skill_dir):
        h = hashlib.sha256()
        for fpath in sorted(skill_dir.rglob("*")):
            if fpath.is_file() and not fpath.is_symlink():
                h.update(fpath.read_bytes())
        expected = h.hexdigest()
        threat = MalwareScanner.verify_skill_hash(skill_dir, expected)
        assert threat is None

    def test_wrong_hash_fails(self, skill_dir):
        threat = MalwareScanner.verify_skill_hash(skill_dir, "0" * 64)
        assert threat is not None
        assert threat.category == ThreatCategory.SUPPLY_CHAIN
        assert threat.level == ThreatLevel.MALICIOUS
        assert "mismatch" in threat.description

    def test_nonexistent_path_fails(self, tmp_path):
        threat = MalwareScanner.verify_skill_hash(tmp_path / "nope", "abc123")
        assert threat is not None
        assert "does not exist" in threat.description

    def test_single_file_hash(self, tmp_path):
        pkg = tmp_path / "skill.tar.gz"
        pkg.write_bytes(b"fake tar content")
        expected = hashlib.sha256(b"fake tar content").hexdigest()
        threat = MalwareScanner.verify_skill_hash(pkg, expected)
        assert threat is None

    def test_tampered_file_detected(self, skill_dir):
        # Compute hash, then modify a file
        h = hashlib.sha256()
        for fpath in sorted(skill_dir.rglob("*")):
            if fpath.is_file():
                h.update(fpath.read_bytes())
        original_hash = h.hexdigest()
        # Tamper
        (skill_dir / "implement.py").write_text("import os; os.system('evil')")
        threat = MalwareScanner.verify_skill_hash(skill_dir, original_hash)
        assert threat is not None
        assert "mismatch" in threat.description


# ── scan_skill integration ────────────────────────────────────────

class TestScanSkillIntegration:

    @pytest.mark.asyncio
    async def test_clean_skill_passes(self, scanner, skill_dir):
        result = await scanner.scan_skill(skill_dir)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_skill_with_symlink_flagged(self, scanner, skill_dir):
        (skill_dir / "link").symlink_to("/etc/hosts")
        result = await scanner.scan_skill(skill_dir)
        assert any(t.category == ThreatCategory.PRIVILEGE_ESCALATION for t in result.threats)

    @pytest.mark.asyncio
    async def test_skill_with_hash_mismatch(self, scanner, skill_dir):
        result = await scanner.scan_skill(skill_dir, expected_hash="0" * 64)
        assert any(t.category == ThreatCategory.SUPPLY_CHAIN for t in result.threats)

    @pytest.mark.asyncio
    async def test_skill_with_binary_extension(self, scanner, skill_dir):
        (skill_dir / "payload.exe").write_bytes(b"\x00" * 10)
        result = await scanner.scan_skill(skill_dir)
        assert any(".exe" in t.description for t in result.threats)
