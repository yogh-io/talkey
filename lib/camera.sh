# The talkey camera: your webcam with the live translation's subtitles, as a virtual camera (v4l2loopback).
CAMERA_LABEL='talkey camera'
camera_device() {
  local node
  for node in "${TALKEY_VIDEO_SYSFS:-/sys/class/video4linux}"/video*; do
    [[ "$(cat "$node/name" 2> /dev/null)" == "$CAMERA_LABEL" ]] || continue
    printf '/dev/%s\n' "${node##*/}"; return 0
  done
  return 1
}
camera_active() { process_alive "$STATE_DIR/camera.pid"; }

camera_start() {
  camera_active && return 0
  local device
  device="$(camera_device)" || { notify "camera: no \"$CAMERA_LABEL\" device - see docs/translate.md" 5000; return 1; }
  TALKEY_STATE_DIR="$STATE_DIR" TALKEY_DATA_DIR="$DATA_DIR" TALKEY_FFMPEG="$FFMPEG" TALKEY_CAMERA_DEVICE="$device" \
    python3 "$SCRIPT_DIR/talkey-camera-worker" 9>&- < /dev/null >> "$STATE_DIR/log" 2>&1 &
  process_save "$!" "$STATE_DIR/camera.pid" || return 1
  state_changed
}

camera_stop() {
  camera_active || return 0
  process_stop "$STATE_DIR/camera.pid" || return 1
  state_changed
  notify 'camera off'
}

# Translate brings the camera up when the device exists. Stopping translate leaves it on, so a call keeps its video.
camera_follow_translate() {
  [[ "${TALKEY_TRANSLATE_CAMERA:-1}" == 1 ]] && camera_device > /dev/null || return 0
  camera_start || true
}

camera_command() {
  case "$ACTION" in
    status)
      local enabled=false device
      camera_active && enabled=true
      device="$(camera_device)" || device=''
      printf '{"enabled":%s,"device":"%s"}\n' "$enabled" "$device" ;;
    start) camera_start && notify "camera on - pick \"$CAMERA_LABEL\" in your call" 4000 ;;
    stop) camera_stop ;;
    toggle) if camera_active; then camera_stop; else camera_start && notify "camera on - pick \"$CAMERA_LABEL\" in your call" 4000; fi ;;
  esac
}
