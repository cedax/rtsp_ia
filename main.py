import subprocess
import cv2
import numpy as np
import threading
import queue
import time
import datetime
from ultralytics import YOLO
import secrets
import string
import os 

# Cargar modelo YOLO
model = YOLO("yolov8n.pt")

# Configuración
rtsp_url = "rtsp://IP/live/ch00_0"
width, height = 1280, 720

# Comando FFmpeg
command = [
    "ffmpeg",
    "-rtsp_transport", "tcp",
    "-i", rtsp_url,
    "-f", "rawvideo",
    "-pix_fmt", "bgr24",
    "-"
]

# Colas
frame_queue = queue.Queue(maxsize=1)

# Bandera de parada
stop_event = threading.Event()

# Variables de grabación
latest_detections = []
recording = False
last_detection_time = 0
video_writer = None

def generar_uid(longitud=10):
    caracteres = string.ascii_letters + string.digits  # A-Z, a-z, 0-9
    return ''.join(secrets.choice(caracteres) for _ in range(longitud))

# Hilo de YOLO
def yolo_worker():
    global latest_detections, last_detection_time
    while not stop_event.is_set():
        try:
            frame = frame_queue.get(timeout=1)
            
            results = model(
                frame, 
                conf=0.6,       # Cambiar a 60% de confianza
                iou=0.45,
                verbose=False
            )[0]

            detections = []
            high_conf_detected = False

            for box in results.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                cls = int(box.cls[0])
                label = model.names[cls]
                detections.append((x1, y1, x2, y2, label, conf))
                
                if conf >= 0.6:  # 60% o más
                    high_conf_detected = True
                    print(f"Detectado: {label} con {conf*100:.2f}% de confianza")

            latest_detections = detections
            
            # Actualizar tiempo de última detección si hay detección de alta confianza
            if high_conf_detected:
                last_detection_time = time.time()
                
            frame_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[YOLO ERROR] {e}")

# Función para iniciar grabación
def start_recording():
    global video_writer
    uid = generar_uid()
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    # Carpeta: recordings/YYYY/MM/DD
    folder_path = os.path.join("recordings", str(now.year), f"{now.month:02d}", f"{now.day:02d}")
    os.makedirs(folder_path, exist_ok=True)

    filename = f"detection_{timestamp}_{uid}.mp4"
    full_path = os.path.join(folder_path, filename)

    # Codificador mp4v para .mp4 (más compatible para web)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    video_writer = cv2.VideoWriter(full_path, fourcc, 20.0, (width, height))

    print(f"Iniciando grabación: {full_path}")
    return full_path

# Función para detener grabación
def stop_recording():
    global video_writer
    if video_writer:
        video_writer.release()
        video_writer = None
        print("Grabación detenida")

# Iniciar hilo de detección
thread = threading.Thread(target=yolo_worker, daemon=True)
thread.start()

# Iniciar FFmpeg
process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

try:
    while True:
        # Leer frame
        raw = process.stdout.read(width * height * 3)
        if len(raw) != width * height * 3:
            print("No se pudo leer frame completo")
            break

        frame = np.frombuffer(raw, np.uint8).reshape((height, width, 3))

        # Enviar a detección solo si hay espacio
        if not frame_queue.full():
            frame_queue.put_nowait(frame.copy())

        # Dibujar detecciones
        annotated = frame.copy()
        for x1, y1, x2, y2, label, conf in latest_detections:
            if conf >= 0.6:  # Solo mostrar detecciones de alta confianza
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(
                    annotated,
                    f"{label} {conf*100:.1f}%",
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2
                )

        # Control de grabación
        current_time = time.time()
        
        # Iniciar grabación si hay detección reciente y no está grabando
        if not recording and (current_time - last_detection_time) < 1:
            recording = True
            start_recording()
        
        # Detener grabación si han pasado 5 segundos sin detección
        elif recording and (current_time - last_detection_time) >= 5:
            recording = False
            stop_recording()
        
        # Grabar frame si está en modo grabación
        if recording and video_writer:
            video_writer.write(annotated)
        
        # Mostrar estado de grabación en pantalla
        if recording:
            cv2.putText(annotated, "GRABANDO", (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        # Mostrar video
        cv2.imshow("Detección YOLO Auto-Record", annotated)

        # Salida con tecla 'q'
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    # Detener grabación si está activa
    if recording:
        stop_recording()
    
    stop_event.set()
    process.terminate()
    thread.join()
    cv2.destroyAllWindows()
    print("Sistema detenido")