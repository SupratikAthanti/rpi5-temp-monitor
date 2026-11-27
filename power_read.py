#!/usr/bin/env python3
"""PMIC rail power reading for the PiStats project.

Parses the raw output of ``vcgencmd pmic_read_adc``.  A rail only contributes
power when it has at least one amps reading *and* at least one volts reading:

    watts(rail) = max(voltage readings) * sum(current readings)

Rails that are voltage-only (including the explicitly excluded EXT5V and BATT
rails) are omitted, because they do not represent on-board consumption.

Two ADC line formats are accepted:

* the real Raspberry Pi 5 firmware output, where the value is embedded in the
  second whitespace-separated token::

      3V7_WL_SW_A current(0)=0.10247270A
      3V7_WL_SW_V volt(8)=3.65894600V

* the legacy three-field fixture format ``RAIL A 1.23`` / ``RAIL V 3.30``.

Current rails (``BASE_A``) and their matching voltage rails (``BASE_V``) are
normalised to the same base name before combining; a bare ``BASE`` works for
both units as well.
"""

import math
import os
import re
import subprocess
import sys

# These rails are measured for monitoring only; they must never be counted as
# board power, even when a (spurious) current reading is present.  Names are
# normalised to their base form, so ``EXT5V_V`` / ``EXT5V_A`` / ``EXT5V`` are
# all excluded, as are the equivalent BATT spellings.
EXCLUDE_VOLTAGE_ONLY_RAILS = {"EXT5V_V", "BATT_V"}
_EXCLUDE_BASES = {"EXT5V", "BATT"}

# A permissive finite number: integer, decimal, or scientific notation.
_NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'

# Real pmic_read_adc line: "RAIL current(0)=0.123A" / "RAIL volt(8)=3.30V".
# Trailing tokens are ignored, whitespace around every component is optional
# and the index inside the parentheses is optional as well.
_REAL_LINE_RE = re.compile(
    r'^(?P<rail>\S+)\s+(?P<kind>current|volt)\s*\(\s*\d*\s*\)\s*=\s*'
    r'(?P<value>' + _NUMBER + r')\s*(?P<unit>[AaVv])?',
    re.IGNORECASE)

# Legacy fixture line: "RAIL 1.23A" (the value and unit in one token).
_VALUE_UNIT_RE = re.compile(
    r'^(' + _NUMBER + r')\s*([AaVv])$', re.IGNORECASE)


def _finite(value):
    """Return a finite float, or ``None`` for missing / malformed values."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _rail_base(rail):
    """Normalise a rail name to ``(BASE, UNIT_SUFFIX)``.

    A trailing ``_A`` / ``_V`` is stripped so ``3V7_WL_SW_A`` and
    ``3V7_WL_SW_V`` share the base ``3V7_WL_SW``.  Names such as ``EXT5V``
    (which merely end in ``V``) are left untouched.
    """
    name = str(rail).strip()
    upper = name.upper()
    if len(name) >= 2 and name[-2] == '_' and upper[-1] in ('A', 'V'):
        return name[:-2].upper(), upper[-1]
    return upper, None


def _parse_line(line):
    """Return ``(base, unit, value)`` for one ADC line, or ``None``."""
    text = str(line).strip()
    if not text:
        return None

    # Preferred path: real firmware output (value inside the second token).
    match = _REAL_LINE_RE.match(text)
    if match:
        kind = match.group('kind').lower()
        unit = 'A' if kind == 'current' else 'V'
        suffix = (match.group('unit') or '').upper()
        if suffix and suffix != unit:
            return None
        value = _finite(match.group('value'))
        if value is None:
            return None
        return _rail_base(match.group('rail'))[0], unit, value

    # Fallback path: legacy three-field fixtures such as "RAIL A 1.23".
    tokens = text.split()
    if len(tokens) < 2:
        return None
    base = _rail_base(tokens[0])[0]
    for index, token in enumerate(tokens[1:], 1):
        upper = token.upper()
        if upper in ('A', 'V') and index + 1 < len(tokens):
            value = _finite(tokens[index + 1])
            if value is not None:
                return base, upper, value
        value = _finite(token)
        if value is not None and index + 1 < len(tokens):
            next_unit = tokens[index + 1].upper()
            if next_unit in ('A', 'V'):
                return base, next_unit, value
        pair = _VALUE_UNIT_RE.match(token)
        if pair:
            value = _finite(pair.group(1))
            if value is not None:
                return base, pair.group(2).upper(), value
    return None


def parse_pmic_adc(text):
    """Return ``{rail_base: watts}`` parsed from raw pmic_read_adc text.

    Malformed lines are skipped, whitespace is arbitrary, exactly duplicated
    ``(rail, unit, value)`` triples are collapsed and multiple distinct current
    readings for one rail are summed (dual-phase rails).  Current and voltage
    names are normalised to a common base before they are combined.
    """
    rails = {}
    if not text:
        return rails

    seen = set()
    currents = {}
    voltages = {}

    for line in str(text).splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        rail, unit, value = parsed
        if rail in _EXCLUDE_BASES:
            continue
        triple = (rail, unit, value)
        if triple in seen:
            continue
        seen.add(triple)
        target = currents if unit == 'A' else voltages
        target.setdefault(rail, []).append(value)

    for rail, amps in currents.items():
        if rail not in voltages:
            continue
        rails[rail] = max(voltages[rail]) * sum(amps)
    return rails


def sum_pmic_watts(source):
    """Sum rail watts from raw ADC text or from a ``parse_pmic_adc`` dict."""
    if isinstance(source, dict):
        return float(sum(float(v) for v in source.values()))
    return float(sum(parse_pmic_adc(source).values()))


def _adc_text(env=None):
    environ = os.environ if env is None else env
    fake = environ.get("FAKE_PMIC_ADC")
    if fake is not None:
        return fake
    try:
        proc = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def read_power(env=None):
    """Return a live power snapshot dict.

    ``{"rails": {...}, "pmic_w": float, "wall_w": float,
       "wall_factor": float}``

    The optional ``env`` mapping is mainly a test hook; by default the process
    environment is used (``FAKE_PMIC_ADC`` and the config's ``wall_factor``).
    """
    environ = os.environ if env is None else env
    rails = parse_pmic_adc(_adc_text(environ))
    pmic_w = float(sum(rails.values()))

    wall_factor = 1.0
    try:
        import pistats_config
        wall_factor = float(pistats_config.load_config().get("wall_factor", 1.0))
    except Exception:
        wall_factor = 1.0

    # Honour an explicit WALL_FACTOR override (used by the collector hooks).
    override = environ.get("WALL_FACTOR")
    if override not in (None, ""):
        try:
            wall_factor = float(override)
        except (TypeError, ValueError):
            pass
    if not math.isfinite(wall_factor) or wall_factor <= 0:
        wall_factor = 1.0

    return {
        "rails": rails,
        "pmic_w": pmic_w,
        "wall_w": pmic_w * wall_factor,
        "wall_factor": wall_factor,
    }


if __name__ == "__main__":
    # Small helper used by collect.sh: read raw ADC text on stdin and print the
    # summed PMIC watts.  FAKE_PMIC_ADC still takes precedence when set.
    raw = os.environ.get("FAKE_PMIC_ADC")
    if raw is None:
        raw = sys.stdin.read()
    print(f"{sum_pmic_watts(raw):.6f}")
