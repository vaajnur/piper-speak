#!/bin/bash
# Piper Speak — озвучка выделенного текста (точка входа для горячей клавиши)
# Путь для KDE:  $HOME/piper-speak/speak.sh

APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
VOICES_DIR="$APP_DIR/voices"
UI_BIN="$APP_DIR/ui.py"
VOICE_CONF="$APP_DIR/voice.conf"
SPEED_CONF="$APP_DIR/speed.conf"

PIPER_BIN="/usr/bin/piper-tts"
LOG="/tmp/speak_piper.log"
WAV="/tmp/speak_piper.wav"
SOCK="/tmp/speak_piper_mpv.sock"
RUNNING="/tmp/speak_piper.running"
STOP_TS="/tmp/speak_piper.stop_ts"
START_TS="/tmp/speak_piper.start_ts"
CANCEL="/tmp/speak_piper.cancel"

log() {
    echo "$(date '+%F %T'): $*" >> "$LOG"
}

now_ms() {
    date +%s%3N 2>/dev/null || echo $(($(date +%s) * 1000))
}

age_ms() {
    local then now
    [ -f "$1" ] || { echo 999999; return; }
    then=$(cat "$1" 2>/dev/null || echo 0)
    now=$(now_ms)
    echo $((now - then))
}

notify() {
    command -v notify-send >/dev/null 2>&1 || return 0
    notify-send -r 42421 -a "Piper" -t 2500 "$1" "$2" 2>/dev/null || \
        notify-send -a "Piper" -t 2500 "$1" "$2" 2>/dev/null || true
}

kill_pid_tree() {
    local pid=$1 child
    [ -n "$pid" ] || return 0
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        kill_pid_tree "$child"
    done
    kill -TERM "$pid" 2>/dev/null || true
}

stop_all() {
    if [ -S "$SOCK" ]; then
        printf '%s\n' '{ "command": ["quit"] }' | socat - "$SOCK" >/dev/null 2>&1 || true
    fi
    if [ -f "$RUNNING" ]; then
        while read -r pid; do
            kill_pid_tree "$pid"
        done < "$RUNNING"
    fi
    pkill -x "piper-tts" 2>/dev/null || true
    rm -f "$SOCK" "$WAV" "$RUNNING" "$START_TS" "$CANCEL"
    now_ms > "$STOP_TS"
}

is_active() {
    [ -f "$RUNNING" ] || return 1
    while read -r pid; do
        [ -n "$pid" ] || continue
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    done < "$RUNNING"
    rm -f "$RUNNING" "$START_TS"
    return 1
}

resolve_model() {
    local name model
    name=$(tr -d '[:space:]' < "$VOICE_CONF" 2>/dev/null || true)
    [ -n "$name" ] || name="irina"
    model="$VOICES_DIR/ru_RU-${name}-medium.onnx"
    if [ ! -f "$model" ]; then
        model=$(ls "$VOICES_DIR"/ru_RU-*-medium.onnx 2>/dev/null | head -n1 || true)
    fi
    printf '%s' "$model"
}

resolve_speed() {
    local speed
    speed=$(tr -d '[:space:]' < "$SPEED_CONF" 2>/dev/null || true)
    case "$speed" in
        1|1.0|1.25|1.5|1.75|2|2.0) printf '%s' "$speed" ;;
        *) printf '%s' "1.0" ;;
    esac
}

# --- toggle ---
if is_active || [ -S "$SOCK" ]; then
    if [ "$(age_ms "$START_TS")" -lt 1200 ]; then
        log "игнор повторного вызова при старте (антидребезг)"
        exit 0
    fi
    stop_all
    log "остановлено горячей клавишей"
    notify "Piper" "Остановлено"
    exit 0
fi

if [ -f "$STOP_TS" ] && [ "$(age_ms "$STOP_TS")" -lt 1000 ]; then
    log "игнор повторного запуска после стопа (антидребезг)"
    exit 0
fi

TEXT=$(wl-paste --primary 2>/dev/null || true)
if [ -z "$TEXT" ]; then
    TEXT=$(wl-paste 2>/dev/null || true)
fi
TEXT=$(printf '%s' "$TEXT" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')

if [ -z "$TEXT" ]; then
    log "пустой буфер/выделение"
    notify "Piper" "Нет текста для озвучки"
    exit 0
fi

MODEL="$(resolve_model)"
VOICE_NAME=$(basename "$MODEL" | sed -n 's/^ru_RU-\(.*\)-medium\.onnx$/\1/p')
SPEED="$(resolve_speed)"
CHARS="${#TEXT}"

if [ ! -x "$PIPER_BIN" ] || [ ! -f "$MODEL" ] || [ ! -f "$UI_BIN" ]; then
    log "нет piper/модели/ui (model=$MODEL)"
    notify "Piper" "Не найдены файлы Piper"
    exit 1
fi

if ! command -v mpv >/dev/null 2>&1; then
    log "не найден mpv"
    notify "Piper" "Не найден mpv"
    exit 1
fi

rm -f "$SOCK" "$WAV" "$STOP_TS" "$CANCEL"
now_ms > "$START_TS"

(
    echo "$BASHPID" > "$RUNNING"

    cleanup() {
        for child in $(pgrep -P "$BASHPID" 2>/dev/null); do
            kill_pid_tree "$child"
        done
        rm -f "$SOCK" "$WAV" "$RUNNING" "$CANCEL"
    }
    trap cleanup EXIT

    log "синтез $CHARS символов голосом $VOICE_NAME"

    # Прелоад сразу — до долгого синтеза
    SPEAK_PIPER_SOCK="$SOCK" \
    SPEAK_PIPER_APP_DIR="$APP_DIR" \
    SPEAK_PIPER_VOICE="$VOICE_NAME" \
    SPEAK_PIPER_SPEED="$SPEED" \
    SPEAK_PIPER_CHARS="$CHARS" \
    SPEAK_PIPER_CANCEL="$CANCEL" \
    SPEAK_PIPER_RUNNING="$RUNNING" \
        python3 "$UI_BIN" >/dev/null 2>>"$LOG" &
    UI_PID=$!
    {
        echo "$BASHPID"
        echo "$UI_PID"
    } > "$RUNNING"

    if ! printf '%s' "$TEXT" | "$PIPER_BIN" --model "$MODEL" --output_file "$WAV" 2>>"$LOG"; then
        if [ -f "$CANCEL" ] || [ ! -f "$RUNNING" ]; then
            log "синтез прерван"
            kill "$UI_PID" 2>/dev/null || true
            exit 0
        fi
        log "ошибка синтеза"
        notify "Piper" "Ошибка синтеза"
        kill "$UI_PID" 2>/dev/null || true
        exit 1
    fi

    if [ -f "$CANCEL" ] || [ ! -f "$RUNNING" ]; then
        log "синтез отменён до воспроизведения"
        kill "$UI_PID" 2>/dev/null || true
        exit 0
    fi

    # UI мог закрыться крестиком — не продолжаем
    if ! kill -0 "$UI_PID" 2>/dev/null; then
        log "UI закрыт, выход"
        exit 0
    fi

    mpv --no-terminal --force-window=no --keep-open=no \
        --speed="$SPEED" \
        --input-ipc-server="$SOCK" \
        "$WAV" >/dev/null 2>>"$LOG" &
    MPV_PID=$!
    {
        echo "$BASHPID"
        echo "$MPV_PID"
        echo "$UI_PID"
    } > "$RUNNING"

    log "воспроизведение mpv=$MPV_PID ui=$UI_PID voice=$VOICE_NAME speed=$SPEED"

    wait "$MPV_PID" 2>/dev/null || true
    kill "$UI_PID" 2>/dev/null || true
    log "готово"
) &

WORKER_PID=$!
echo "$WORKER_PID" > "$RUNNING"
log "воркер запущен pid=$WORKER_PID chars=$CHARS"
