"""Prevent accidental publication of credentials and identifying data.

Run from the repository root with::

    python -m unittest discover -s tests -v

The audit examines tracked files and non-ignored untracked files in the
working tree. Findings contain only a relative path, line number, and rule
identifier; matched values are never printed. This is a pragmatic guard, not
a substitute for reviewing changes or scanning Git history.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Build patterns without embedding usable sample credentials in source."""
    token_prefixes = ("gh" + "p_", "gh" + "o_", "gl" + "pat-", "xox" + "b-")
    token_pattern = re.compile(
        r"(?i)(?:" + "|".join(re.escape(prefix) for prefix in token_prefixes)
        + r")[A-Za-z0-9_-]{20,}"
    )
    aws_pattern = re.compile(r"AKIA[A-Z0-9]{16}")
    private_key_pattern = re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    )
    assignment_pattern = re.compile(
        r"(?i)(?:^|[^A-Za-z0-9])(?:api[_-]?key|access[_-]?token|client[_-]?secret|"
        r"password|passwd|secret|token)(?=$|[^A-Za-z0-9])\s*[:=]\s*[\"']?([^\s\"']{8,})"
    )
    email_pattern = re.compile(
        r"(?i)\b[A-Z0-9._%+-]+@(?:[A-Z0-9-]+\.)+[A-Z]{2,}\b"
    )
    home_path_pattern = re.compile(
        r"(?:^|[\s=:'\"(])/(?:home|Users)/[^/\s:'\"),]+"
    )
    cpf_pattern = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")

    return (
        ("PRIVATE_KEY", private_key_pattern),
        ("PROVIDER_TOKEN", token_pattern),
        ("AWS_ACCESS_KEY_ID", aws_pattern),
        ("CREDENTIAL_ASSIGNMENT", assignment_pattern),
        ("EMAIL_ADDRESS", email_pattern),
        ("PERSONAL_HOME_PATH", home_path_pattern),
        ("BRAZILIAN_CPF", cpf_pattern),
    )


PATTERNS = _patterns()


def _is_placeholder(value: str) -> bool:
    if "${" in value:
        return True  # Compose or shell variable reference, not a literal secret.
    normalized = value.strip().strip("\"'`{}$").lower()
    markers = (
        "your_", "your-", "replace_me", "replace-this", "replace-with",
        "changeme", "change-me", "<", "example", "placeholder",
    )
    # Avoid treating ordinary source expressions (for example, token = obj.get(...))
    # as hard-coded values. Provider-specific token formats are checked separately.
    return (
        not normalized
        or any(marker in normalized for marker in markers)
        or any(character in normalized for character in (".", "(", ")", "{", "}"))
    )


def _private_ip_matches(line: str) -> bool:
    # 127/8 and link-local addresses are common local-development settings;
    # RFC1918 and IPv6 unique-local addresses are the network data of interest.
    ipv4_private_ranges = tuple(
        ipaddress.ip_network(".".join(octets) + suffix)
        for octets, suffix in (
            (("10", "0", "0", "0"), "/8"),
            (("172", "16", "0", "0"), "/12"),
            (("192", "168", "0", "0"), "/16"),
        )
    )
    ipv6_private_range = ipaddress.ip_network("fc" + "00::/7")
    for candidate in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", line):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if any(address in network for network in ipv4_private_ranges):
            return True
    for candidate in re.findall(r"(?i)(?<![\w:])(?:fc|fd)[0-9a-f:]{2,}(?![\w:])", line):
        try:
            if ipaddress.ip_address(candidate) in ipv6_private_range:
                return True
        except ValueError:
            continue
    return False


def _ignored_private_prompt() -> bool:
    tracked = _git("ls-files", "--error-unmatch", "system_prompt.md")
    ignored = _git("check-ignore", "--quiet", "system_prompt.md")
    return tracked.returncode != 0 and ignored.returncode == 0


def _candidate_files() -> list[Path]:
    result = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode != 0:
        raise RuntimeError("git ls-files falhou; execute os testes dentro de um clone Git.")
    paths = []
    for item in result.stdout.split("\0"):
        if not item:
            continue
        path = ROOT / item
        if path.is_file():
            paths.append(path)
    return paths


def _sensitive_filename(path: Path) -> bool:
    name = path.name.lower()
    safe_env_templates = {".env.example", ".env.template", ".env.sample"}
    if name.startswith(".env") and name not in safe_env_templates:
        return True
    if name in {"id_rsa", "id_ed25519", "credentials.json", "service-account.json"}:
        return True
    return path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}


def _scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []  # Binary and unreadable files are outside this text audit.

    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        for rule, pattern in PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            if rule == "CREDENTIAL_ASSIGNMENT" and _is_placeholder(match.group(1)):
                continue
            findings.append((line_number, rule))
        if _private_ip_matches(line):
            findings.append((line_number, "PRIVATE_NETWORK_ADDRESS"))
    return findings


class SensitiveDataTests(unittest.TestCase):
    def test_personal_system_prompt_is_ignored_and_untracked(self):
        self.assertTrue(
            _ignored_private_prompt(),
            "system_prompt.md deve permanecer ignorado pelo Git e fora do índice.",
        )

    def test_detector_recognizes_sensitive_categories(self):
        fake_token = "gh" + "p_" + "A" * 24
        samples = [
            "Authorization: " + fake_token,
            "user=" + "conta-teste" + "@example.org",
            "config em /" + "home" + "/usuario/projeto",
            "servidor " + ".".join(("10", "23", "4", "5")),
            "CPF " + "123" + ".456.789-09",
            "-----BEGIN " + "PRIVATE" + " KEY-----",
        ]
        findings = [rule for sample in samples for _, rule in _scan_text(sample)]
        for rule in (
            "PROVIDER_TOKEN",
            "EMAIL_ADDRESS",
            "PERSONAL_HOME_PATH",
            "PRIVATE_NETWORK_ADDRESS",
            "BRAZILIAN_CPF",
            "PRIVATE_KEY",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, findings)

    def test_example_placeholders_and_loopback_are_not_sensitive_findings(self):
        sample = (
            "API_KEY=your_placeholder_value\n"
            "SEARXNG_SECRET=replace-this-with-a-long-random-value\n"
            "SEARXNG_BASE_URL=http://127.0.0.1:8888/"
        )
        self.assertEqual(_scan_text(sample), [])

    def test_sensitive_file_names_are_rejected_but_templates_are_allowed(self):
        self.assertTrue(_sensitive_filename(Path(".env.local")))
        self.assertTrue(_sensitive_filename(Path("private.pem")))
        self.assertFalse(_sensitive_filename(Path(".env.example")))

    def test_public_working_tree_has_no_sensitive_data(self):
        findings = []
        for path in _candidate_files():
            if _sensitive_filename(path):
                findings.append(f"{path.relative_to(ROOT)}:1: SENSITIVE_FILENAME")
            for line_number, rule in _scan_file(path):
                findings.append(f"{path.relative_to(ROOT)}:{line_number}: {rule}")

        self.assertFalse(
            findings,
            "Dados potencialmente sensíveis encontrados (valores omitidos):\n"
            + "\n".join(findings),
        )


def _scan_text(text: str) -> list[tuple[int, str]]:
    """Test helper with the same matching rules, without creating a file."""
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in PATTERNS:
            match = pattern.search(line)
            if match and not (rule == "CREDENTIAL_ASSIGNMENT" and _is_placeholder(match.group(1))):
                findings.append((line_number, rule))
        if _private_ip_matches(line):
            findings.append((line_number, "PRIVATE_NETWORK_ADDRESS"))
    return findings


if __name__ == "__main__":
    unittest.main(verbosity=2)
