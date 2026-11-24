# HUD presentation and meter rendering. Sourced by eww-feed.
# --- level trace: a smooth colour SVG the eww image widget renders --------------
# eww's graph widget is smooth but single-colour; block glyphs colour per-column
# but look stepped. So we draw the meter ourselves as an SVG - a smooth
# Catmull-Rom curve through the level history, filled with a left-to-right gradient
# whose colour at each x is that column's own zone (orange too-quiet / green good /
# red too-loud / grey silence). Written to a fresh file each tick so the image
# widget always reloads it (a constant path would be cached).
HIST_N="${TALKEY_HIST_N:-70}"        # history columns (curve resolution)
SVG_W="${TALKEY_SVG_W:-600}"; SVG_H="${TALKEY_SVG_H:-90}"   # viewBox; the widget scales it
C_GOOD="#38d07f"; C_LOW="#e2953a"; C_LOUD="#f2573f"; C_SIL="#4b555f"
LVL_HIST=(); LAST_SZ=0; LAST_TAKE=""; TICK=0      # persistent across ticks (feed is one long-lived loop)
SVGDIR="$STATE_DIR/meter"; mkdir -p "$SVGDIR"; rm -f "$SVGDIR"/m-*.svg 2>/dev/null

# build_svg PATH - write a smooth, per-column-coloured area chart of LVL_HIST.
build_svg() {
  local out="$1"
  printf '%s\n' "${LVL_HIST[@]}" | awk -v W="$SVG_W" -v H="$SVG_H" -v CAP="$HIST_N" \
      -v FLOOR="$FLOOR" -v LOW="$LOW" -v LOUD="$LOUD" \
      -v CG="$C_GOOD" -v CL="$C_LOW" -v CH="$C_LOUD" -v CS="$C_SIL" '
    { v[NR] = $1 }
    END {
      n = NR; top = 3; base = H - 3;
      printf "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 %d %d\" preserveAspectRatio=\"none\">", W, H;
      if (n < 2) { printf "</svg>"; exit }
      # FIXED time-scale: each sample is W/(CAP-1) wide. The trace fills from the
      # LEFT and the area/stroke end exactly at the newest sample - everything to
      # its right stays EMPTY until the trace grows there. Once CAP samples have
      # built up it spans the full width and then scrolls (oldest drops off).
      dx = W / (CAP - 1);
      for (i = 1; i <= n; i++) {
        x[i] = (i - 1) * dx;                     # i=1 (oldest) at the left; empty to the right of x[n]
        pk = v[i];
        if (pk < FLOOR) lvl = 0; else { lvl = (pk - FLOOR) * 100 / (0 - FLOOR); if (lvl < 0) lvl = 0; if (lvl > 100) lvl = 100 }
        y[i] = base - lvl / 100 * (base - top);
        col[i] = (pk < FLOOR) ? CS : (pk < LOW) ? CL : (pk < LOUD) ? CG : CH;
      }
      # gradient in user space over the whole viewBox; one stop per sample x
      printf "<defs><linearGradient id=\"g\" x1=\"0\" y1=\"0\" x2=\"%d\" y2=\"0\" gradientUnits=\"userSpaceOnUse\">", W;
      for (i = 1; i <= n; i++) printf "<stop offset=\"%.4f\" stop-color=\"%s\"/>", x[i] / W, col[i];
      printf "</linearGradient></defs>";
      # smooth path: MONOTONE cubic (Fritsch-Carlson). Unlike Catmull-Rom its curve
      # never overshoots past the samples - the slope is flattened at every peak and
      # valley, so a spike stays at its real height instead of bulging above it.
      for (i = 1; i < n; i++) sec[i] = (y[i+1] - y[i]) / (x[i+1] - x[i]);   # secants
      m[1] = sec[1]; m[n] = sec[n-1];                                       # end tangents
      for (i = 2; i < n; i++) m[i] = (sec[i-1] * sec[i] <= 0) ? 0 : (sec[i-1] + sec[i]) / 2;
      for (i = 1; i < n; i++) {                                            # clamp so it stays monotone
        if (sec[i] == 0) { m[i] = 0; m[i+1] = 0; }
        else { a = m[i]/sec[i]; b = m[i+1]/sec[i]; s = a*a + b*b;
               if (s > 9) { t = 3/sqrt(s); m[i] = t*a*sec[i]; m[i+1] = t*b*sec[i]; } }
      }
      d = sprintf("M%.1f,%.1f", x[1], y[1]);
      for (i = 1; i < n; i++) {
        h = (x[i+1] - x[i]) / 3;
        d = d sprintf(" C%.1f,%.1f %.1f,%.1f %.1f,%.1f", x[i]+h, y[i]+m[i]*h, x[i+1]-h, y[i+1]-m[i+1]*h, x[i+1], y[i+1]);
      }
      printf "<path d=\"%s L%.1f,%d L%.1f,%d Z\" fill=\"url(#g)\" fill-opacity=\"0.80\"/>", d, x[n], base, x[1], base;
      printf "<path d=\"%s\" fill=\"none\" stroke=\"url(#g)\" stroke-width=\"2.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>", d;
      printf "</svg>";
    }' > "$out"
}

emit() {
  local pkdb=-99 lvl=0 zone=silence header transcript footer secs long
  local take; take="$(readlink -f "$STATE_DIR/dictate.current" 2>/dev/null)"
  if [[ "$take" != "$LAST_TAKE" ]]; then LVL_HIST=(); LAST_SZ=0; LAST_TAKE="$take"; fi
  local conf=-1 sil=0 peak=-99 pflag="" footertext footerkind timetext metricstext="" pzone="" pw
  if [[ -s "$RAW" ]]; then
    pkdb=$(tail -c 4096 "$RAW" 2>/dev/null | od -An -td2 -v 2>/dev/null | awk '
      { for(i=1;i<=NF;i++){ v=$i; a=(v<0)?-v:v; if(a>pk)pk=a; n++ } }
      END{ if(!n||pk<1){ print -99; exit }
           printf "%d", int(20*log(pk/32768)/log(10) - 0.5) }')
  fi
  [[ "$pkdb" =~ ^-?[0-9]+$ ]] || pkdb=-99
  if (( pkdb >= FLOOR )); then
    lvl=$(( (pkdb - FLOOR) * 100 / (0 - FLOOR) )); (( lvl < 0 )) && lvl=0; (( lvl > 100 )) && lvl=100
    if   (( pkdb < LOW ));  then zone=low
    elif (( pkdb < LOUD )); then zone=good
    else                        zone=loud; fi
  fi
  # Grow the level trace only when the recording actually grows. Idle (no audio) ->
  # empty trace; file shrank -> a new dictation started, so reset; unchanged (e.g.
  # 'done') -> freeze the last real trace instead of appending a flat tail.
  local sz; sz=$(stat -Lc%s "$RAW" 2>/dev/null || echo 0)
  if   (( sz == 0 ));       then : # retain the last meter while the final result is visible
  elif (( sz <  LAST_SZ )); then LVL_HIST=("$pkdb")
  elif (( sz >  LAST_SZ )); then LVL_HIST+=("$pkdb")
  fi
  (( ${#LVL_HIST[@]} > HIST_N )) && LVL_HIST=("${LVL_HIST[@]: -HIST_N}")
  LAST_SZ=$sz
  # Render to a fresh file each tick (constant path would be cached), emit its path,
  # and reap the one from a few ticks ago so the dir doesn't grow.
  TICK=$((TICK + 1)); local meterpath="$SVGDIR/m-$TICK.svg"
  build_svg "$meterpath"
  rm -f "$SVGDIR/m-$((TICK - 4)).svg" 2>/dev/null
  header="$(cat "$HD" 2>/dev/null)"; header="${header:-listening}"
  local headertext="${header^^}"   # GTK CSS has no text-transform, so uppercase here
  # Fold to a fixed width, then pin the transcript to EXACTLY LINES rows so the
  # panel is a constant height and nothing below it (graph, footer) ever shifts.
  # Newest line sits at the bottom; older lines scroll up and off the top; when
  # there are fewer than LINES they are padded with blank rows above (a space keeps
  # the row height). eww's :wrap won't wrap and :limit-width truncates, so we insert
  # the line breaks ourselves.
  transcript="$(cat "$TR" 2>/dev/null | fold -s -w "${TALKEY_EWW_COLS:-52}" | awk -v L="${TALKEY_EWW_LINES:-4}" '
    { a[NR] = $0 }
    END {
      if (NR == 0) { for (k = 0; k < L-1; k++) print " "; print "speak…"; exit }
      start = (NR > L) ? NR-L+1 : 1;
      for (k = 0; k < L-NR; k++) print " ";      # top padding when short (bottom-aligned)
      for (i = start; i <= NR; i++) print a[i];
    }')"
  footer="$(cat "$FT" 2>/dev/null)"; footer="${footer:-type}"
  local bytes="$sz" rate="${TALKEY_SAMPLE_RATE:-16000}" channels="${TALKEY_CHANNELS:-1}"
  if [[ -s "$take/bytes" ]]; then bytes="$(cat "$take/bytes")"; fi
  if [[ -s "$take/audio.format" ]]; then read -r rate channels < "$take/audio.format"; fi
  secs=$(( bytes / (rate * channels * 2) ))
  long=$(( secs >= WARN_S ? 1 : 0 ))

  case "$footer" in
    send)           footertext="⏎ will send";                     footerkind=send ;;
    cancel)         footertext="✖ will cancel";                   footerkind=cancel ;;
    restart_armed)  footertext="$L_MEMO tap again to restart";     footerkind=restart ;;
    *)              footertext="tap $L_SUBMIT send  ·  $L_MEMO twice restarts";  footerkind=plain ;;
  esac
  if [[ -f "$take/auto.owner" ]]; then
    if [[ "$footerkind" == plain ]]; then footertext="tap $L_SUBMIT send"; fi
    footertext+="  ·  ${TALKEY_LABEL_DICTATE:-dictate} finishes phrase"
  fi
  local tap=0 now
  [[ ! -s "$take/memo.presstime" ]] || tap="$(cat "$take/memo.presstime")"
  now="$(date +%s%3N)"
  if [[ -f "$STATE_DIR/dictate.pid" ]] && (( now - tap < ${TALKEY_DOUBLE_TAP_MS:-400} )); then
    footertext="$L_MEMO tap again to restart"; footerkind=restart
  fi
  if (( secs > ${TALKEY_MAX_PUMP_S:-60} )); then footertext="live preview paused · $footertext"; fi
  (( long )) && timetext="${secs}s ⚠ long" || timetext="${secs}s"

  if [[ -s "$MET" ]]; then
    read -r conf sil peak _cflag pflag < "$MET" 2>/dev/null || true
    [[ "$conf" =~ ^-?[0-9]+$ ]] || conf=-1
    [[ "$sil"  =~ ^[0-9]+$   ]] || sil=0
    [[ "$peak" =~ ^-?[0-9]+$ ]] || peak=-99
    case "$pflag" in loud) pw="too loud"; pzone=loud ;; low) pw="too low"; pzone=low ;; *) pw="good"; pzone=good ;; esac
    if (( conf >= 0 )); then metricstext="conf ${conf}%  silence ${sil}%  peak ${peak}dB  ${pw}"
    else                     metricstext="silence ${sil}%  peak ${peak}dB  ${pw}"; fi
  fi

  jq -cn --arg h "$header" --arg ht "$headertext" --arg t "$transcript" \
         --arg tt "$timetext" --argjson lg "$long" \
         --arg ft "$footertext" --arg fk "$footerkind" \
         --arg mt "$metricstext" --arg pz "$pzone" \
         --argjson l "$lvl" --arg z "$zone" \
         --arg mp "$meterpath" \
     '{header:$h, headertext:$ht, transcript:$t, timetext:$tt, long:($lg==1),
       footertext:$ft, footerkind:$fk, metricstext:$mt, pzone:$pz,
       level:$l, zone:$z, meterpath:$mp}'
}
