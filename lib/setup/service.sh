# ===========================================================================
# service
# ===========================================================================
step_service() {
  step "speech-to-text service"
  if [[ -n "${TALKEY_WHISPER_URL:-}" ]]; then
    if curl -fsS --max-time 3 -o /dev/null "${TALKEY_WHISPER_URL%/inference}/"; then
      ok "your speech server is responding"
      return 0
    fi
    todo "check your speech server: $TALKEY_WHISPER_URL"
    return 1
  fi
  if ! have systemctl; then
    warn "no systemd - start the server yourself: talkey-whisper-server"
    return 0
  fi
  local unit=talkey-whisper.service
  if ! systemctl --user cat "$unit" >/dev/null 2>&1; then
    warn "$unit is not installed"
    info "from a checkout, run setup/install.sh - it installs the units"
    todo "install the systemd units: run setup/install.sh from a talkey checkout"
    return 1
  fi

  # Someone pointing at their own server has no model and no unit of ours to
  # care about, so neither check applies to them.
  if [[ -z "${TALKEY_WHISPER_URL:-}" ]] && ! model_path "${TALKEY_MODEL_NAME:-large-v3-turbo}" >/dev/null; then
    warn "no ${TALKEY_MODEL_NAME:-large-v3-turbo} model, so the server would exit as soon as it started"
    info "get the model first: talkey-setup model"
    todo "install a speech model, then: talkey-setup service"
    return 1
  fi

  if systemctl --user is-active --quiet "$unit"; then
    ok "already running"
  else
    ask_yn "start it now, and at every login?" || {
      info "skipped"
      todo "start the speech service: systemctl --user enable --now $unit"
      return 0
    }
    # An earlier start with no model can leave the unit rate-limited, and it
    # will not come back until the failure count is cleared.
    systemctl --user reset-failed "$unit" 2>/dev/null || true
    systemctl --user enable --now "$unit" || {
      warn "could not start $unit"
      todo "start the speech service: systemctl --user enable --now $unit"
      return 1
    }
  fi

  # A 1.6GB model off cold cache takes far longer than a second to load, so a
  # single immediate check would report a fault that is not there.
  local host="${TALKEY_WHISPER_HOST:-127.0.0.1}" port="${TALKEY_WHISPER_PORT:-8910}"
  printf '   %s-%s waiting for the server on %s:%s ' "$DIM" "$R" "$host" "$port"
  local up=0 dead=0 _
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 -o /dev/null "http://$host:$port/" 2>/dev/null; then up=1; break; fi
    # Stop the moment the unit gives up. Waiting out the full minute at a
    # service that already died just delays the bad news by 50 seconds.
    if ! systemctl --user is-active --quiet "$unit"; then dead=1; break; fi
    printf '.'; sleep 1
  done
  printf '\n'
  if (( up )); then
    ok "responding on $host:$port"
    return 0
  fi
  if (( dead )); then
    warn "the service stopped. Its last words:"
  else
    warn "no answer within 60s. The service is running but not answering:"
  fi
  # Printed here rather than named as a command to go and run - the reason is
  # usually one line, and it is the whole point of this step.
  journalctl --user -u "$unit" -n 5 --no-pager -o cat 2>/dev/null | sed 's/^/     /'
  todo "the speech server is not answering on $host:$port - fix what it says above, then: systemctl --user restart $unit"
  return 1
}
