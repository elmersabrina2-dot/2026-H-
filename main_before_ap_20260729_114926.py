
from maix import camera, display, image, nn, app, comm
import struct, os

report_on = True
APP_CMD_DETECT_RES = 0x02

def encode_objs(objs):
    '''
        encode objs info to bytes body for protocol
        2B x(LE) + 2B y(LE) + 2B w(LE) + 2B h(LE) + 2B idx + 4B score(float) ...
    '''
    body = b''
    for obj in objs:
        body += struct.pack("<hhHHHf", obj.x, obj.y, obj.w, obj.h, obj.class_id, obj.score)
    return body

model_path = "model_298847.mud"
if not os.path.exists(model_path):
    model_path = "/root/726gz/model_298847.mud"
detector = nn.YOLOv5(model=model_path)

cam = camera.Camera(detector.input_width(), detector.input_height(), detector.input_format())
dis = display.Display()

p = comm.CommProtocol(buff_size = 1024)

while not app.need_exit():
    # msg = p.get_msg()

    img = cam.read()
    objs = detector.detect(img, conf_th = 0.5, iou_th = 0.45)

    if len(objs) > 0 and report_on:
        body = encode_objs(objs)
        p.report(APP_CMD_DETECT_RES, body)

    for obj in objs:
        img.draw_rect(obj.x, obj.y, obj.w, obj.h, color = image.COLOR_RED)
        msg = f'{detector.labels[obj.class_id]}: {obj.score:.2f}'
        img.draw_string(obj.x, obj.y, msg, color = image.COLOR_RED)
    dis.show(img)

