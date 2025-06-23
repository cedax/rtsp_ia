import subprocess
import cv2
import numpy as np
import threading
import queue
from ultralytics import YOLO

# Cargar modelo YOLO
model = YOLO("yolov8n.pt")

# Configuración
rtsp_url = "rtsp://sedax:_Pumasi10203900_@52.147.203.239:8450/live/ch00_0"
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
result_queue = queue.Queue(maxsize=1)

# Bandera de parada
stop_event = threading.Event()

# Última detección disponible (fuera de las colas)
latest_detections = []

# Hilo de YOLO
def yolo_worker():
    global latest_detections
    while not stop_event.is_set():
        try:
            frame = frame_queue.get(timeout=1)
            
            #results = model(frame, verbose=False)[0]

            results = model(
                frame, 
                conf=0.25,      # Umbral de confianza (default 0.25)
                iou=0.45,       # Non-Maximum Suppression (default 0.45)
                verbose=False
            )[0]

            detections = []

            for box in results.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                cls = int(box.cls[0])
                label = model.names[cls]
                detections.append((x1, y1, x2, y2, label, conf))

                # Mostrar en consola
                print(f"Detectado: {label} con {conf*100:.2f}% de confianza")

            latest_detections = detections  # Actualizar detecciones globales
            frame_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[YOLO ERROR] {e}")

# Iniciar hilo de detección
thread = threading.Thread(target=yolo_worker, daemon=True)
thread.start()

# Iniciar FFmpeg
process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

try:
    while True:
        # Leer frame sin detener el flujo
        raw = process.stdout.read(width * height * 3)
        if len(raw) != width * height * 3:
            print("No se pudo leer frame completo")
            break

        frame = np.frombuffer(raw, np.uint8).reshape((height, width, 3))

        # Enviar a detección solo si hay espacio
        if not frame_queue.full():
            frame_queue.put_nowait(frame.copy())

        # Dibujar detecciones más recientes (si hay)
        annotated = frame.copy()

        for x1, y1, x2, y2, label, conf in latest_detections:
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

        # Mostrar video siempre actualizado
        cv2.imshow("Detección YOLO (fluido)", annotated)

        # Salida con tecla 'q'
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    stop_event.set()
    process.terminate()
    thread.join()
    cv2.destroyAllWindows()