from maix import (
    app,
    camera,
    display,
    err,
    image,
    nn,
    pinmap,
    time,
    uart,
)
import math
import os
import struct

try:
    from maix import network
except ImportError:
    network = None

try:
    from maix import rtsp
except ImportError:
    rtsp = None


# =====================================================================
# User configuration
# =====================================================================

MODEL_FILE = "model_298847.mud"
BALL_CLASS_ID = 0

# High-resolution raw video channel for the receiver laptop/PAD.
RTSP_ENABLED = True
STREAM_WIDTH = 1280
STREAM_HEIGHT = 720
STREAM_FPS = 30
STREAM_PORT = 8554
STREAM_BITRATE = 3_000_000

# Default: MaixCAM creates a dedicated 2.4 GHz access point. Connect the
# receiver laptop directly to it; no phone hotspot or router is required.
# Options:
#   "external_router": use the Wi-Fi connection configured in MaixCAM Settings.
#   "maixcam_ap": MaixCAM creates its own Wi-Fi access point.
NETWORK_MODE = "maixcam_ap"

# Optional automatic connection when NETWORK_MODE == "external_router".
# Keeping this False avoids storing the competition router password in code.
WIFI_AUTO_CONNECT = False
WIFI_SSID = ""
WIFI_PASSWORD = ""

# Used only when NETWORK_MODE == "maixcam_ap".
AP_SSID = "MaixCAM-Ball"
AP_PASSWORD = "maixcam2026"
AP_MODE = "g"       # 2.4 GHz for maximum laptop/tablet compatibility.
AP_CHANNEL = 6
AP_IP = "192.168.66.1"

# MaixCAM UART1 is recommended for communication with MSPM0G3507.
UART_TX_PIN = "A19"
UART_RX_PIN = "A18"
UART_DEVICE = "/dev/ttyS1"
UART_BAUD = 115200

# YOLO and single-target validation.
DETECT_CONF_TH = 0.50
DETECT_IOU_TH = 0.45
MIN_BOX_SIZE = 4
MAX_BOX_SIZE = 90
MIN_ASPECT = 0.50
MAX_ASPECT = 2.00
MAX_TRACK_JUMP_PX = 48.0
COAST_FRAMES = 2
REACQUIRE_AFTER_MISSES = 4

# Two-point calibration in the 224 x 224 recognition channel.
# Enable LOCAL_PREVIEW and DEBUG_DRAW, place the ball at exactly -5 cm
# and +5 cm, then replace these two pixel coordinates.
CAL_NEG_PIXEL = (70.0, 112.0)
CAL_NEG_CM = -5.0
CAL_POS_PIXEL = (154.0, 112.0)
CAL_POS_CM = 5.0

# Keep False until the two calibration points above have been measured.
# UART continues sending STATUS_LOST heartbeats while it is False.
CALIBRATION_READY = False

# Adaptive EMA: stable near the set point, quicker during fast motion.
EMA_ALPHA_SLOW = 0.32
EMA_ALPHA_FAST = 0.68
EMA_FAST_ERROR_CM = 0.80

# Camera settings. Keep automatic exposure for the first test.
# After measuring a good value at the competition site, set both values
# greater than zero to use deterministic manual exposure/gain.
MANUAL_EXPOSURE_US = 0
MANUAL_GAIN = 0

# Competition mode: no local preview and no overlays. The RTSP stream is
# always the clean 1280 x 720 raw camera view.
LOCAL_PREVIEW = False
DEBUG_DRAW = False
DEBUG_LOG = True
DEBUG_PRINT_INTERVAL_MS = 1000

# Camera read timeouts can occur when RTSP and the AI channel run together.
CAMERA_RETRY_MS = 10
TIMEOUT_LOG_INTERVAL_MS = 2000


# =====================================================================
# UART packet
# =====================================================================

# Fixed 8-byte frame:
#   [0] 0x55
#   [1] 0xAA
#   [2] status: 0=lost/invalid, 1=current measurement, 2=short coast
#   [3] signed position int16 little-endian, low byte
#   [4] signed position int16 little-endian, high byte
#   [5] confidence 0..100
#   [6] sequence 0..255
#   [7] checksum = sum(bytes 0..6) & 0xFF
#
# Position unit: 0.1 mm/LSB. +5.00 cm is sent as +500.
STATUS_LOST = 0
STATUS_MEASURED = 1
STATUS_COAST = 2


def make_packet(status, position_cm, confidence, sequence):
    position_0p1mm = int(round(position_cm * 100.0))
    position_0p1mm = max(-32768, min(32767, position_0p1mm))
    confidence_u8 = max(0, min(100, int(round(confidence * 100.0))))

    payload = struct.pack(
        "<BBBhBB",
        0x55,
        0xAA,
        status,
        position_0p1mm,
        confidence_u8,
        sequence,
    )
    return payload + bytes([sum(payload) & 0xFF])


def open_control_uart():
    err.check_raise(
        pinmap.set_pin_function(UART_TX_PIN, "UART1_TX"),
        "Failed to map A19 to UART1_TX",
    )
    err.check_raise(
        pinmap.set_pin_function(UART_RX_PIN, "UART1_RX"),
        "Failed to map A18 to UART1_RX",
    )
    return uart.UART(UART_DEVICE, UART_BAUD)


# =====================================================================
# Network and RTSP
# =====================================================================

def setup_network():
    if network is None:
        raise RuntimeError("This MaixPy firmware has no network module")

    wifi = network.wifi.Wifi()
    ap_started_by_app = False

    if NETWORK_MODE == "maixcam_ap":
        # A station connection and AP mode may conflict on some firmware
        # versions. Disconnect the station side before starting our AP.
        if wifi.is_connected():
            err.check_raise(
                wifi.disconnect(),
                "Failed to disconnect Wi-Fi station before AP mode",
            )
            time.sleep_ms(500)
        if wifi.is_ap_mode():
            err.check_raise(
                wifi.stop_ap(),
                "Failed to stop the previous Wi-Fi AP",
            )
            time.sleep_ms(500)
        err.check_raise(
            wifi.start_ap(
                AP_SSID,
                AP_PASSWORD,
                mode=AP_MODE,
                channel=AP_CHANNEL,
                ip=AP_IP,
            ),
            "Failed to start MaixCAM Wi-Fi AP",
        )
        ap_started_by_app = True
        print("Wi-Fi AP:", AP_SSID)
        print("AP IP:", AP_IP)
        return wifi, ap_started_by_app

    if NETWORK_MODE != "external_router":
        raise RuntimeError("Invalid NETWORK_MODE: " + NETWORK_MODE)

    if WIFI_AUTO_CONNECT and not wifi.is_connected():
        if not WIFI_SSID:
            raise RuntimeError("WIFI_SSID is empty")
        err.check_raise(
            wifi.connect(WIFI_SSID, WIFI_PASSWORD, wait=True, timeout=30),
            "Failed to connect to external Wi-Fi router",
        )

    ip = wifi.get_ip()
    if ip:
        print("Wi-Fi IP:", ip)
    else:
        print(
            "WARNING: no Wi-Fi IP. Configure Wi-Fi in Settings, "
            "but AI and UART will continue."
        )
    return wifi, ap_started_by_app


def start_rtsp(stream_camera):
    if not RTSP_ENABLED:
        return None
    if rtsp is None:
        print("WARNING: this MaixPy firmware has no RTSP module")
        return None

    server = rtsp.Rtsp(
        port=STREAM_PORT,
        fps=STREAM_FPS,
        bitrate=STREAM_BITRATE,
    )
    try:
        err.check_raise(
            server.bind_camera(stream_camera),
            "Failed to bind 1280x720 camera to RTSP",
        )
        err.check_raise(server.start(), "Failed to start RTSP server")

        urls = server.get_urls()
        if urls:
            for url in urls:
                print("RTSP:", url)
        else:
            print("RTSP:", server.get_url())
        return server
    except Exception as ex:
        print("WARNING: RTSP disabled, AI and UART continue:", ex)
        try:
            server.stop()
        except Exception:
            pass
        return None


# =====================================================================
# Model, calibration, detection and tracking
# =====================================================================

def find_model_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, MODEL_FILE),
        MODEL_FILE,
        "/root/726gz/" + MODEL_FILE,
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise RuntimeError("Cannot find " + MODEL_FILE)


def object_center(obj):
    return obj.x + obj.w * 0.5, obj.y + obj.h * 0.5


def valid_ball_candidate(obj):
    if obj.class_id != BALL_CLASS_ID or obj.score < DETECT_CONF_TH:
        return False
    if obj.w < MIN_BOX_SIZE or obj.h < MIN_BOX_SIZE:
        return False
    if obj.w > MAX_BOX_SIZE or obj.h > MAX_BOX_SIZE:
        return False
    aspect = obj.w / float(max(1, obj.h))
    return MIN_ASPECT <= aspect <= MAX_ASPECT


def select_ball(objs, previous_center, miss_count):
    candidates = [obj for obj in objs if valid_ball_candidate(obj)]
    if not candidates:
        return None

    # Coarse acquisition/recovery.
    if previous_center is None or miss_count >= REACQUIRE_AFTER_MISSES:
        return max(candidates, key=lambda obj: obj.score)

    # Per-frame refinement near the previous center.
    px, py = previous_center
    best = None
    best_cost = 1e9
    best_distance = 1e9
    for obj in candidates:
        cx, cy = object_center(obj)
        distance = math.sqrt((cx - px) ** 2 + (cy - py) ** 2)
        cost = distance - 8.0 * obj.score
        if cost < best_cost:
            best = obj
            best_cost = cost
            best_distance = distance

    if best_distance > MAX_TRACK_JUMP_PX:
        return None
    return best


def pixel_to_position_cm(pixel_x, pixel_y):
    x0, y0 = CAL_NEG_PIXEL
    x1, y1 = CAL_POS_PIXEL
    axis_x = x1 - x0
    axis_y = y1 - y0
    axis_len_sq = axis_x * axis_x + axis_y * axis_y
    if axis_len_sq < 1.0:
        raise RuntimeError("Invalid calibration points")

    ratio = ((pixel_x - x0) * axis_x + (pixel_y - y0) * axis_y) / axis_len_sq
    return CAL_NEG_CM + ratio * (CAL_POS_CM - CAL_NEG_CM)


def filter_position(measured_cm, filtered_cm):
    if filtered_cm is None:
        return measured_cm
    error = measured_cm - filtered_cm
    alpha = EMA_ALPHA_FAST if abs(error) >= EMA_FAST_ERROR_CM else EMA_ALPHA_SLOW
    return filtered_cm + alpha * error


def lost_status(filtered_cm, miss_count):
    if CALIBRATION_READY and filtered_cm is not None and miss_count <= COAST_FRAMES:
        return STATUS_COAST
    return STATUS_LOST


# =====================================================================
# Main
# =====================================================================

def main():
    wifi = None
    ap_started_by_app = False
    stream_camera = None
    detect_camera = None
    rtsp_server = None
    local_screen = None

    control_uart = open_control_uart()
    detector = nn.YOLOv5(model=find_model_path())

    try:
        try:
            wifi, ap_started_by_app = setup_network()
        except Exception as ex:
            print("WARNING: network setup failed, AI and UART continue:", ex)
            wifi = None
            ap_started_by_app = False

        # Create the high-resolution NV21 channel first. RTSP takes ownership
        # of this channel after binding, so it must never be read directly.
        try:
            stream_camera = camera.Camera(
                STREAM_WIDTH,
                STREAM_HEIGHT,
                image.Format.FMT_YVU420SP,
                fps=STREAM_FPS,
                buff_num=3,
            )

            # Create the independent low-resolution RGB recognition channel
            # before binding the main channel to RTSP.
            detect_camera = stream_camera.add_channel(
                detector.input_width(),
                detector.input_height(),
                detector.input_format(),
                fps=STREAM_FPS,
                buff_num=2,
            )

            if MANUAL_EXPOSURE_US > 0:
                stream_camera.exposure(MANUAL_EXPOSURE_US)
            if MANUAL_GAIN > 0:
                stream_camera.gain(MANUAL_GAIN)

            rtsp_server = start_rtsp(stream_camera)
        except Exception as ex:
            print(
                "WARNING: dual camera channels unavailable; "
                "falling back to AI+UART only:",
                ex,
            )
            if detect_camera is not None:
                try:
                    detect_camera.close()
                except Exception:
                    pass
            detect_camera = None
            if stream_camera is not None:
                try:
                    stream_camera.close()
                except Exception:
                    pass
            stream_camera = None
            detect_camera = camera.Camera(
                detector.input_width(),
                detector.input_height(),
                detector.input_format(),
                fps=STREAM_FPS,
                buff_num=2,
            )
            if MANUAL_EXPOSURE_US > 0:
                detect_camera.exposure(MANUAL_EXPOSURE_US)
            if MANUAL_GAIN > 0:
                detect_camera.gain(MANUAL_GAIN)

        if LOCAL_PREVIEW:
            local_screen = display.Display()

        previous_center = None
        filtered_cm = None
        last_confidence = 0.0
        miss_count = REACQUIRE_AFTER_MISSES
        sequence = 0
        last_debug_ms = 0
        last_timeout_log_ms = 0
        timeout_count = 0

        time.fps_start()

        while not app.need_exit():
            img = None
            try:
                img = detect_camera.read()
            except Exception as ex:
                timeout_count += 1
                miss_count += 1
                last_confidence = 0.0
                status = lost_status(filtered_cm, miss_count)
                position_cm = filtered_cm if filtered_cm is not None else 0.0
                control_uart.write(
                    make_packet(status, position_cm, last_confidence, sequence)
                )
                sequence = (sequence + 1) & 0xFF

                now_ms = time.ticks_ms()
                if (
                    DEBUG_LOG
                    and now_ms - last_timeout_log_ms >= TIMEOUT_LOG_INTERVAL_MS
                ):
                    print(
                        "AI camera timeout count=%d, retrying: %s"
                        % (timeout_count, ex)
                    )
                    last_timeout_log_ms = now_ms
                time.sleep_ms(CAMERA_RETRY_MS)
                continue

            try:
                objs = detector.detect(
                    img,
                    conf_th=DETECT_CONF_TH,
                    iou_th=DETECT_IOU_TH,
                )
            except Exception as ex:
                miss_count += 1
                last_confidence = 0.0
                status = lost_status(filtered_cm, miss_count)
                position_cm = filtered_cm if filtered_cm is not None else 0.0
                control_uart.write(
                    make_packet(status, position_cm, last_confidence, sequence)
                )
                sequence = (sequence + 1) & 0xFF
                if DEBUG_LOG:
                    print("YOLO inference error:", ex)
                time.sleep_ms(1)
                continue

            ball = select_ball(objs, previous_center, miss_count)

            if ball is not None:
                center_x, center_y = object_center(ball)
                measured_cm = pixel_to_position_cm(center_x, center_y)
                filtered_cm = filter_position(measured_cm, filtered_cm)
                previous_center = (center_x, center_y)
                last_confidence = ball.score
                miss_count = 0
                status = STATUS_MEASURED if CALIBRATION_READY else STATUS_LOST
            else:
                miss_count += 1
                last_confidence = 0.0
                status = lost_status(filtered_cm, miss_count)
                if miss_count >= REACQUIRE_AFTER_MISSES:
                    previous_center = None

            position_cm = filtered_cm if filtered_cm is not None else 0.0
            packet = make_packet(
                status,
                position_cm,
                last_confidence,
                sequence,
            )
            control_uart.write(packet)
            sequence = (sequence + 1) & 0xFF

            fps = time.fps()

            if LOCAL_PREVIEW:
                img.draw_string(2, 2, "FPS: %.1f" % fps, color=image.COLOR_RED)
                if DEBUG_DRAW and ball is not None:
                    img.draw_rect(
                        ball.x,
                        ball.y,
                        ball.w,
                        ball.h,
                        color=image.COLOR_RED,
                    )
                    img.draw_cross(
                        int(center_x),
                        int(center_y),
                        color=image.COLOR_RED,
                    )
                    img.draw_string(
                        2,
                        24,
                        "px=(%.1f,%.1f) pos=%.2fcm"
                        % (center_x, center_y, position_cm),
                        color=image.COLOR_RED,
                    )
                local_screen.show(img)

            now_ms = time.ticks_ms()
            if DEBUG_LOG and now_ms - last_debug_ms >= DEBUG_PRINT_INTERVAL_MS:
                if ball is not None:
                    print(
                        "fps=%.1f px=(%.1f,%.1f) pos=%.3fcm "
                        "score=%.2f status=%d uart=%s"
                        % (
                            fps,
                            center_x,
                            center_y,
                            position_cm,
                            ball.score,
                            status,
                            packet.hex(),
                        )
                    )
                else:
                    print(
                        "fps=%.1f ball=lost pos=%.3fcm status=%d uart=%s"
                        % (fps, position_cm, status, packet.hex())
                    )
                last_debug_ms = now_ms

    finally:
        # Properly release VENC/RTSP resources so the next app start does not
        # fail with a stale encoder-resource error.
        if rtsp_server is not None:
            try:
                rtsp_server.stop()
            except Exception:
                pass
        if detect_camera is not None:
            try:
                detect_camera.close()
            except Exception:
                pass
        if stream_camera is not None:
            try:
                stream_camera.close()
            except Exception:
                pass
        if local_screen is not None:
            try:
                local_screen.close()
            except Exception:
                pass
        if wifi is not None and ap_started_by_app:
            try:
                wifi.stop_ap()
            except Exception:
                pass


if __name__ == "__main__":
    main()
