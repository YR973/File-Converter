# Source this file in Bash on a headless Linux cloud machine.
# Uses the installed Xorg dummy driver; does not expose the display over TCP.
if [ -z "${DISPLAY:-}" ]; then
    export DISPLAY=:99
fi
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    converter_display_dir="$(mktemp -d /tmp/file-converter-display.XXXXXX)"
    cat > "$converter_display_dir/xorg.conf" <<'CONFIG'
Section "Device"
 Identifier "Dummy"
 Driver "dummy"
 VideoRam 256000
EndSection
Section "Monitor"
 Identifier "Monitor"
 HorizSync 5.0-1000.0
 VertRefresh 5.0-200.0
 Modeline "1280x800" 83.5 1280 1352 1480 1680 800 803 809 831
EndSection
Section "Screen"
 Identifier "Screen"
 Device "Dummy"
 Monitor "Monitor"
 DefaultDepth 24
 SubSection "Display"
  Depth 24
  Modes "1280x800"
 EndSubSection
EndSection
CONFIG
    nohup Xorg "$DISPLAY" -config "$converter_display_dir/xorg.conf" \
        -logfile "$converter_display_dir/xorg.log" -nolisten tcp -noreset \
        >"$converter_display_dir/server.log" 2>&1 </dev/null &
    converter_display_ready=0
    for converter_display_attempt in $(seq 1 50); do
        if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
            converter_display_ready=1
            break
        fi
        sleep 0.1
    done
    if [ "$converter_display_ready" != 1 ]; then
        cat "$converter_display_dir/server.log" >&2
        return 1
    fi
fi
