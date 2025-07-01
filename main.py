import subprocess
import cv2
import numpy as np
import threading
import queue
import time
import datetime
import json
import os
import secrets
import string
from ultralytics import YOLO
from collections import deque

# Configuración
model = YOLO("yolov8n.pt")
rtsp_url = "rtsp://IP/live/ch00_0"
width, height = 1280, 720
fps = 20
command = ["ffmpeg", "-rtsp_transport", "tcp", "-i", rtsp_url, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]

# Variables globales
frame_queue = queue.Queue(maxsize=1)
stop_event = threading.Event()
detections = []
recording = False
last_detection_time = 0
video_writer = None
detection_log = []
current_video_path = None
conf_threshold = 0.2

# Buffer circular para pregrabación
prebuffer_seconds = 5
prebuffer = deque(maxlen=prebuffer_seconds * fps)

seconds_to_stop_before_last_detection = 5

def generate_uid(length=10):
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))

def yolo_worker():
    global detections, last_detection_time, conf_threshold
    while not stop_event.is_set():
        try:
            frame = frame_queue.get(timeout=1)
            results = model(frame, conf=conf_threshold, iou=0.45, verbose=False)[0]
            detections = [
                (int(b.xyxy[0][0]), int(b.xyxy[0][1]), int(b.xyxy[0][2]), int(b.xyxy[0][3]),
                 model.names[int(b.cls[0])], float(b.conf[0])) for b in results.boxes
            ]
            if any(conf >= conf_threshold for *_, conf in detections):
                last_detection_time = time.time()
                for *_, label, conf in detections:
                    if conf >= conf_threshold:
                        print(f"Detectado: {label} con {conf*100:.2f}% de confianza")
            frame_queue.task_done()
        except (queue.Empty, Exception) as e:
            if not isinstance(e, queue.Empty):
                print(f"[YOLO ERROR] {e}")

def start_recording():
    global video_writer, current_video_path, detection_log
    detection_log = []
    now = datetime.datetime.now()
    folder_path = os.path.join("recordings", str(now.year), f"{now.month:02d}", f"{now.day:02d}")
    os.makedirs(folder_path, exist_ok=True)

    filename = f"detection_{now.strftime('%Y%m%d_%H%M%S')}_{generate_uid()}.mp4"
    current_video_path = os.path.join(folder_path, filename)

    # Configuración optimizada para streaming web
    # Usando H.264 con configuración web-friendly
    fourcc = cv2.VideoWriter_fourcc(*'avc1')  # H.264 codec
    video_writer = cv2.VideoWriter(current_video_path, fourcc, fps, (width, height))
    
    # Verificar si el writer se inicializó correctamente
    if not video_writer.isOpened():
        print("Error: No se pudo inicializar VideoWriter con avc1, probando con mp4v...")
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        video_writer = cv2.VideoWriter(current_video_path, fourcc, fps, (width, height))
    
    print(f"Iniciando grabación: {current_video_path}")

    # Escribir frames del prebuffer
    for buffered_frame in prebuffer:
        video_writer.write(buffered_frame)

def stop_recording():
    global video_writer, current_video_path, detection_log
    if video_writer:
        video_writer.release()
        video_writer = None
        print("Grabación detenida")

        if current_video_path:
            # Post-procesamiento con FFmpeg para optimizar streaming
            optimize_for_web(current_video_path)
            
            json_path = os.path.splitext(current_video_path)[0] + ".json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"video": os.path.basename(current_video_path), "detections": detection_log}, f, indent=2)
            print(f"Detecciones guardadas en: {json_path}")

def optimize_for_web(video_path):
    """
    Post-procesa el video para optimizarlo para streaming web
    """
    try:
        # Crear nombre del archivo optimizado
        base_name = os.path.splitext(video_path)[0]
        optimized_path = f"{base_name}_web.mp4"
        
        # Comando FFmpeg para optimización web
        ffmpeg_cmd = [
            "ffmpeg", "-y",  # -y para sobrescribir sin preguntar
            "-i", video_path,
            "-c:v", "libx264",           # Codec H.264
            "-preset", "fast",           # Preset rápido para encoding
            "-crf", "23",               # Calidad constante (18-28, menor = mejor calidad)
            "-profile:v", "main",        # Perfil H.264 compatible
            "-level", "3.1",            # Nivel H.264
            "-pix_fmt", "yuv420p",      # Formato de pixel compatible
            "-movflags", "+faststart",   # Optimización para streaming progresivo
            "-maxrate", "2M",           # Bitrate máximo 2Mbps
            "-bufsize", "4M",           # Tamaño del buffer
            "-g", str(fps * 2),         # GOP size (keyframe cada 2 segundos)
            "-sc_threshold", "0",        # Desactivar detección de cambio de escena
            optimized_path
        ]
        
        print(f"Optimizando video para web: {optimized_path}")
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"Video optimizado guardado: {optimized_path}")
            # Opcionalmente, eliminar el archivo original
            # os.remove(video_path)
        else:
            print(f"Error al optimizar video: {result.stderr}")
            
    except Exception as e:
        print(f"Error en optimización: {e}")

# Iniciar hilo de detección y FFmpeg
threading.Thread(target=yolo_worker, daemon=True).start()
process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

try:
    while True:
        raw = process.stdout.read(width * height * 3)
        if len(raw) != width * height * 3:
            break

        frame = np.frombuffer(raw, np.uint8).reshape((height, width, 3))

        # Enviar a detección
        if not frame_queue.full():
            frame_queue.put_nowait(frame.copy())

        # Dibujar detecciones
        annotated = frame.copy()
        for x1, y1, x2, y2, label, conf in detections:
            if conf >= conf_threshold:
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(annotated, f"{label} {conf*100:.1f}%", (x1, y1-10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        # Agregar al prebuffer
        prebuffer.append(annotated.copy())

        # Guardar detecciones si está grabando
        if recording and detections:
            timestamp = datetime.datetime.now().isoformat()
            detection_log.extend([
                {
                    "timestamp": timestamp,
                    "label": label,
                    "confidence": round(conf, 3),
                    "box": [x1, y1, x2, y2]
                }
                for x1, y1, x2, y2, label, conf in detections if conf >= conf_threshold
            ])

        # Control de grabación
        current_time = time.time()
        if not recording and (current_time - last_detection_time) < 1:
            recording = True
            start_recording()
        elif recording and (current_time - last_detection_time) >= seconds_to_stop_before_last_detection:
            recording = False
            stop_recording()

        # Grabar frame
        if recording and video_writer:
            video_writer.write(annotated)

        # Mostrar estado y video
        if recording:
            cv2.putText(annotated, "GRABANDO", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        cv2.imshow("Camara", annotated)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    if recording:
        stop_recording()
    stop_event.set()
    process.terminate()
    cv2.destroyAllWindows()
    print("Sistema detenido")