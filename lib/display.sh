# Sourced by bin/talkey. See docs/architecture.md for ownership rules.

overlay_metrics() {
  local alp sil peak
  read -r alp sil peak < "$STT_METRICS" 2>/dev/null || true
  if [[ -z "${alp:-}" && -z "${sil:-}" ]]; then : > "$METRICS_FILE"; return 0; fi
  # "none" is the no-confidence sentinel (no jq, or no segments). It must be a
  # real field: an empty one would shift silence and peak left into the wrong
  # variables, and exp() of a percentage renders a nonsense confidence.
  [[ "$alp" == none ]] && alp=""
  # conf from avg_logprob (exp -> %); silence is already a percent. conf=-1 means
  # no confidence available (jq-less path) - the overlay then shows silence only.
  # pflag grades the peak into three zones: "loud" at/above PEAK_LOUD_DB (red),
  # "low" below PEAK_LOW_DB (orange), "good" in between (green). peak=-99 (no
  # audio) falls into low.
  awk -v a="${alp:-}" -v sil="${sil:-0}" -v peak="${peak:--99}" \
      -v cw="$CONF_WARN" -v sw="$SIL_WARN" -v loud="$PEAK_LOUD_DB" -v low="$PEAK_LOW_DB" 'BEGIN{
    hasc = (a != "");
    conf = hasc ? int(exp(a)*100+0.5) : -1;
    cflag = (((hasc && conf<cw) || sil>sw) ? "warn" : "ok");
    pflag = (peak>=loud) ? "loud" : ((peak<low) ? "low" : "good");
    printf "%d %d %d %s %s", conf, sil, peak, cflag, pflag
  }' > "$METRICS_FILE" 2>/dev/null || : > "$METRICS_FILE"
}

prime_display() {
  : > "$TRANSCRIPT"; : > "$METRICS_FILE"; printf 'listening' > "$HEADER_FILE"; printf '%s' "$(current_submit_footer)" > "$FOOTER_FILE"
}

current_submit_footer() {
  if   [[ -f "$SUBMIT_REQ" ]]; then echo send
  elif [[ -f "$CANCEL_REQ" ]]; then echo cancel
  else echo type; fi
}

overlay_on() { [[ "$STREAM" == 1 ]]; }
overlay_state() { printf '%s' "$1" > "$HEADER_FILE"; state_changed; }
overlay_body() { printf '%s' "$1" > "$TRANSCRIPT"; state_changed; }
overlay_footer() { printf '%s' "$1" > "$FOOTER_FILE"; state_changed; }
