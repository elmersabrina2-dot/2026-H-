# MaixCAM双通道钢珠识别、RTSP图传和UART输出

## 已实现

- `1280×720 / 30 FPS / NV21`高清主通道，绑定H.264 RTSP服务器；
- `224×224 / RGB`副通道，运行原有YOLOv5钢珠模型；
- 钢珠候选验证、最近中心跟踪、短时丢帧保持和自适应EMA；
- 两点像素—厘米标定；
- UART1、115200向MSPM0G3507发送钢珠位置；
- AI副通道读取超时时发送安全心跳并在10 ms后重试；
- Wi-Fi或RTSP启动失败时自动保留AI识别和UART控制；
- 双摄像头通道不可用时自动退回224×224单通道识别；
- RTSP、Wi-Fi、摄像头资源的退出清理；
- 默认创建MaixCAM专用2.4 GHz热点；
- Windows接收端RTSP无损录像脚本。

RTSP图像是未经检测框污染的高清原始画面。无线图传断开不会停止本地AI识别和UART控制。

## 部署

将以下文件放在MaixCAM应用的同一目录：

- `main.py`
- `app.yaml`
- `model_298847.mud`
- `model_298847.cvimodel`

建议先升级到具有`camera.add_channel()`、`rtsp`和`network.wifi`接口的较新MaixPy v4固件。

## 网络配置

默认配置：

```python
NETWORK_MODE = "maixcam_ap"
WIFI_AUTO_CONNECT = False
```

不需要先在MaixCAM系统设置中连接路由器，也不需要手机热点。应用启动后，电脑直接连接MaixCAM创建的热点：

```text
SSID: MaixCAM-Ball
Password: maixcam2026
```

然后用VLC打开：

```text
RTSP: rtsp://192.168.66.1:8554/live
```

热点固定为2.4 GHz、频道6、MaixCAM地址`192.168.66.1`。如果比赛现场频道6干扰严重，可把`AP_CHANNEL`改成1或11。

如以后改用外部路由器，只需把`NETWORK_MODE`改为`"external_router"`，并先在MaixCAM系统设置中连接该路由器。

## 接线

| MaixCAM | MSPM0G3507 |
| --- | --- |
| A19 / UART1_TX | 所选UART的RX |
| A18 / UART1_RX | 所选UART的TX |
| GND | GND |

串口参数为`115200、8N1、3.3 V`。

## UART协议

固定8字节：

| 字节 | 内容 |
| --- | --- |
| 0 | `0x55` |
| 1 | `0xAA` |
| 2 | 0=丢失/无效，1=本帧测量，2=短时保持 |
| 3 | 位置低字节 |
| 4 | 位置高字节 |
| 5 | 置信度0–100 |
| 6 | 帧序号 |
| 7 | 前7字节累加和低8位 |

位置为有符号`int16`小端数，单位0.1 mm：

- `+5.00 cm → +500`
- `-5.00 cm → -500`

MSPM0在状态0时必须停止使用该位置；状态2只用于很短的检测丢帧。

## 标定

初始代码设置：

```python
CALIBRATION_READY = False
LOCAL_PREVIEW = False
DEBUG_DRAW = False
```

标定时修改为：

```python
LOCAL_PREVIEW = True
DEBUG_DRAW = True
```

1. 把钢珠准确放在`-5.0 cm`，读取日志或屏幕的`px=(x,y)`，填入`CAL_NEG_PIXEL`。
2. 把钢珠放在`+5.0 cm`，填入`CAL_POS_PIXEL`。
3. 用`-5 cm、0 cm、+5 cm`检查误差。
4. 设置`CALIBRATION_READY = True`。
5. 比赛时恢复`LOCAL_PREVIEW = False`和`DEBUG_DRAW = False`。

摄像头高度、角度、分辨率或裁剪方式变化后，需要重新标定。

## 接收和录像

VLC或OBS可直接打开打印出的RTSP地址。

Windows电脑也可以在连接`MaixCAM-Ball`热点后，右键`view_rtsp.ps1`选择“使用PowerShell运行”。脚本会自动寻找VLC或ffplay并打开固定的RTSP地址。

如果电脑安装了ffmpeg，也可以在PowerShell运行：

```powershell
.\record_rtsp.ps1 -Url "rtsp://192.168.66.1:8554/live"
```

程序保存MKV文件；比赛前5–10秒开始录制，测试结束后按`Ctrl+C`停止。MKV在意外断电时通常比直接写MP4更易恢复。

## 首轮测试

1. 暂时不接电机，只检查720p画面能否完整覆盖整个摆杆。
2. 连续运行15分钟，记录RTSP是否卡断、日志中`AI camera timeout`累计次数。
3. 检查识别FPS，目标不低于20 FPS。
4. 完成两点标定，检查`-5 cm、0 cm、+5 cm`静态误差和抖动。
5. 用MSPM0调试器检查正负值、序号和校验和。
6. 断开接收端Wi-Fi，确认UART识别仍继续运行。

## 官方API参考

- https://wiki.sipeed.com/maixpy/api/maix/camera.html
- https://wiki.sipeed.com/maixpy/api/maix/rtsp.html
- https://wiki.sipeed.com/maixpy/api/maix/network/wifi.html
- https://wiki.sipeed.com/maixpy/doc/en/faq.html
