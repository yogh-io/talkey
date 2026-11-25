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
# Each feed draws into its own directory, so one never removes the other's frames.
SVGDIR="${SVGDIR:-$STATE_DIR/meter}"; mkdir -p "$SVGDIR"; rm -f "$SVGDIR"/m-*.svg 2>/dev/null

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

# peak FILE - "dB level zone" of the last ~0.13 s of 16-bit audio in FILE.
peak() {
  local pkdb=-99 lvl=0 zone=silence
  if [[ -s "$1" ]]; then
    pkdb=$(tail -c 4096 "$1" 2>/dev/null | od -An -td2 -v 2>/dev/null | awk '
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
  printf '%s %s %s' "$pkdb" "$lvl" "$zone"
}

# rows TEXT EMPTY - the transcript as a fixed number of rows, newest at the bottom.
rows() {
  printf '%s' "$1" | fold -s -w "${TALKEY_EWW_COLS:-52}" | awk -v L="${TALKEY_EWW_LINES:-4}" -v E="$2" '
    { a[NR] = $0 }
    END {
      if (NR == 0) { for (k = 0; k < L-1; k++) print " "; print E; exit }
      start = (NR > L) ? NR-L+1 : 1;
      for (k = 0; k < L-NR; k++) print " ";      # top padding when short (bottom-aligned)
      for (i = start; i <= NR; i++) print a[i];
    }'
}

emit() {
  local pkdb=-99 lvl=0 zone=silence header transcript footer secs long
  local take; take="$(readlink -f "$STATE_DIR/dictate.current" 2>/dev/null)"
  if [[ "$take" != "$LAST_TAKE" ]]; then LVL_HIST=(); LAST_SZ=0; LAST_TAKE="$take"; fi
  local conf=-1 sil=0 peak=-99 pflag="" footertext footerkind timetext metricstext="" pzone="" pw
  read -r pkdb lvl zone < <(peak "$RAW")
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
  transcript="$(rows "$(cat "$TR" 2>/dev/null)" "speak…")"
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

# --- live translation: the session's phrases, and where the broadcast is ---------
# The view keeps SUB_ROWS rows of SUB_COLS characters, so the window never changes
# size. The worker gives each piece of English the Unix time it starts and stops
# playing; where the voice is inside a piece is estimated from its characters.
SUB_ROWS=14; SUB_COLS=84
SUB_MONO="UbuntuMono Nerd Font Mono, Ubuntu Mono, DejaVu Sans Mono, monospace"
# Colours from the eww.scss palette: dim Dutch, bright English, the piece on air on teal.
# shellcheck disable=SC2016
SUB_JQ='
def esc: gsub("&"; "&amp;") | gsub("<"; "&lt;") | gsub(">"; "&gt;");
def words: [splits(" +")] | map(select(length > 0));
def look: {
  nl: "foreground=\"#9aa3ab\"", heard: "foreground=\"#7f868c\" style=\"italic\"",
  played: "foreground=\"#dfe3e6\"", said: "foreground=\"#ffffff\" background=\"#1e5e68\"",
  next: "foreground=\"#9fd3da\" background=\"#1e5e68\"",
  queued: "foreground=\"#6b737a\" style=\"italic\"", wait: "foreground=\"#6b737a\""}[.];
def wrap: if length == 0 then [] else
  reduce .[] as $x ([[]];
    (.[-1] | map(.w | length + 1) | add // 0) as $used
    | if (.[-1] | length) > 0 and $used + ($x.w | length) > $cols then . + [[$x]] else .[-1] += [$x] end)
  end;
def markup: reduce .[] as $x ([];
    if length == 0 then [{s: $x.s, t: $x.w, sep: ""}]
    elif .[-1].s == $x.s then .[-1].t += " " + $x.w
    elif .[-1].s == "said" and $x.s == "next" then . + [{s: $x.s, t: " " + $x.w, sep: ""}]
    else . + [{s: $x.s, t: $x.w, sep: " "}] end)
  | map(.sep + "<span \(.s | look)>\(.t | esc)</span>") | join("");

[.lines[].pieces[]? | select(.start != null and .end != null and .end > .start)] as $played
| ([$played[].text | length] | add // 0) as $chars
| (if $chars > 0 then ([$played[] | .end - .start] | add) / $chars else 0.065 end) as $rate
| def on_air: .start != null and .start <= $now and (.end == null or $now < .end);
  def piece_words: (.text | words) as $w
  | if .start == null or $now < .start then $w | map({w: ., s: "queued"})
    elif .end != null and $now >= .end then $w | map({w: ., s: "played"})
    else
      ([if .end != null then .end - .start else (.text | length) * $rate end, 0.001] | max) as $d
      | (($now - .start) / $d * (.text | length)) as $at
      | reduce range(0; $w | length) as $i ({out: [], c: 0};
          .out += [{w: $w[$i], s: (if $i == 0 or .c <= $at then "said" else "next" end)}]
          | .c += ($w[$i] | length) + 1)
      | .out
    end;
  def phrase_rows: . as $l
  | (if .dutch == "" then [{w: "…", s: "wait"}] else .dutch | words | map({w: ., s: (if $l.live then "heard" else "nl" end)}) end) as $nl
  | ([.pieces[]? | piece_words[]] + (if .done then [] else [{w: "…", s: "wait"}] end)) as $en
  | ($nl | wrap | to_entries | map({n: $l.n, g: (if .key == 0 then "»" else " " end), r: .value}))
    + ($en | wrap | to_entries | map({n: $l.n, g: (if .key == 0 then "→" else " " end), r: .value}));
[.lines[].n] as $ns
| (if $anchor == null then ($ns | length) else [$ns[] | select(. <= $anchor)] | length end) as $bottom
| ([0, $bottom - $rows] | max) as $first
| [.lines[$first:$bottom][] | phrase_rows[]] as $above
| ($first == 0 and ($above | length) <= $rows) as $top
| (if $top then [.lines[:$rows][] | phrase_rows[]][:$rows] else $above[-$rows:] end) as $view
| ($view[-1].n // null) as $last
| ([$ns[] | select($last != null and . > $last)]) as $newer
| (if $view == [] then [{g: " ", r: [{w: "speak…", s: "wait"}]}] else $view end) as $view
| ([range(0; $rows - ($view | length)) | {g: " ", r: []}] + $view
   | map("<span face=\"\($mono)\" foreground=\"#565c62\">\(.g) </span>"
         + (if (.r | length) == 0 then " " else .r | markup end))
   | join("\n")) as $text
| (if $top or $last == null then "stay" else ([$ns[] | select(. < $last)] | last // "stay" | tostring) end) as $up
| (if $anchor != null then {status: "↓ \($newer | length) NEWER", kind: "back", hint: "scroll down for the newest  ·  "}
   elif any(.lines[].pieces[]?; on_air) then {status: "▶ ON AIR", kind: "onair"}
   else {status: "", kind: "plain"} end) as $state
| {
    headertext: ($header + (if .lines[-1].live // false then " · HEARING" else "" end)),
    status: $state.status, statuskind: $state.kind, rows: $text,
    footertext: "\($state.hint // (if $up == "stay" then "" else "scroll up to look back  ·  " end))double-tap \($memo) to stop",
    enginetext: (if $engine == "" then "" else "engine: " + $engine end),
    level: $level, zone: $zone, meterpath: $meter,
    up: $up,
    down: (if ($newer | length) <= 1 then "live" else ($newer[0] | tostring) end),
    busy: any(.lines[].pieces[]?; .start != null and (.end == null or .end > $now))
  }'

# emit_subtitles - one frame of the translate window; BUSY=1 while English is on air or due.
emit_subtitles() {
  local pkdb lvl zone stamp meterpath anchor now subs frame header engine
  read -r pkdb lvl zone < <(peak "$LEVEL")
  # The worker refreshes translate.raw while a phrase shows; each refresh is one step of the trace.
  stamp="$(stat -Lc%Y.%y "$LEVEL" 2>/dev/null)"
  if [[ -n "$stamp" && "$stamp" != "$LAST_LEVEL" ]]; then LVL_HIST+=("$pkdb"); LAST_LEVEL="$stamp"; fi
  (( ${#LVL_HIST[@]} > HIST_N )) && LVL_HIST=("${LVL_HIST[@]: -HIST_N}")
  TICK=$((TICK + 1)); meterpath="$SVGDIR/m-$TICK.svg"
  build_svg "$meterpath"
  rm -f "$SVGDIR/m-$((TICK - 4)).svg" 2>/dev/null
  anchor="$(cat "$SCROLL" 2>/dev/null)"; [[ "$anchor" =~ ^[0-9]+$ ]] || anchor=null
  # EPOCHREALTIME writes the locale's decimal mark; jq wants a dot.
  now="${EPOCHREALTIME%??????}"; now="${now%?}.${EPOCHREALTIME: -6}"
  subs="$(cat "$SUBS" 2>/dev/null)"; [[ -n "$subs" ]] || subs='{"lines":[]}'
  header="${TALKEY_TRANSLATE_FROM:-nl} → en"
  engine="$(cat "$STATE_DIR/translate.current/engine" 2>/dev/null)"
  frame="$(jq -c --argjson now "$now" --argjson anchor "$anchor" --argjson rows "$SUB_ROWS" \
             --argjson cols "$SUB_COLS" --arg mono "$SUB_MONO" --arg header "${header^^}" \
             --arg memo "$L_MEMO" --argjson level "$lvl" --arg zone "$zone" --arg meter "$meterpath" \
             --arg engine "$engine" \
             "$SUB_JQ" <<< "$subs")" || { BUSY=0; return; }
  BUSY=0; [[ "$frame" != *'"busy":true'* ]] || BUSY=1
  printf '%s\n' "$frame"
}
