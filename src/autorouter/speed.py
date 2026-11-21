"""Predict a local speed recommendation. Native hosts own speed overrides and billing."""
import argparse
import json
import math
from pathlib import Path


def recommend(forecast):
    manual = forecast.get("manual")
    tiers = ("standard", "fast", "ultrafast")
    if manual in tiers:
        return {"tier": manual, "reason": "manual"}
    preference = forecast.get("preference", "balanced")
    if preference not in {"economy", "balanced", "speed-first"}:
        raise ValueError("Unknown performance preference")
    def standard(reason):
        return {"tier": "standard", "reason": reason}
    if preference == "economy" or forecast.get("interactive") is not True:
        return standard("economy or background")
    if forecast.get("residency_allowed") is not True:
        return standard("residency approval missing")
    if forecast.get("rate_limited") or forecast.get("provider_degraded"):
        return standard("provider cooldown")
    supported = forecast.get("supported", [])
    costs = forecast.get("premium_upper_bound", {})
    seconds = forecast.get("model_seconds", {})
    def number(value):
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return value
        return None
    budget = number(forecast.get("remaining_budget"))
    baseline = number(seconds.get("standard"))
    tools = number(forecast.get("tool_seconds"))
    if budget is None or not baseline or tools is None or baseline+tools<3:
        return standard("insufficient telemetry or short task")
    previous = forecast.get("previous")
    default = 0.15 if preference == "speed-first" else 0.25
    threshold = number(forecast.get("minimum_gain", default))
    if threshold is None or not 0 < threshold <= 1:
        raise ValueError("minimum_gain must be greater than zero and at most one")
    for tier in reversed(tiers[1:]):
        if tier == "ultrafast" and forecast.get("allow_ultrafast") is not True:
            continue
        cost, expected = number(costs.get(tier)), number(seconds.get(tier))
        if tier not in supported or cost is None or cost > budget or expected is None:
            continue
        gain = (baseline - expected) / (baseline + tools)
        if gain >= (threshold * 0.65 if previous == tier else threshold):
            return {"tier": tier, "reason": "bounded critical-path saving",
                    "estimated_saved_seconds": round(baseline-expected, 1)}
    return standard("premium or latency threshold")


def main(argv):
    parser = argparse.ArgumentParser(prog="autorouter speed")
    parser.add_argument("--forecast", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.forecast.read_text(encoding="utf-8"))
        print(json.dumps(recommend(data), indent=2))
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        parser.exit(2, f"Invalid speed forecast: {exc}\n")
