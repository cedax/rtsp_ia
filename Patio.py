from dotenv import load_dotenv
import os
import ffmpeg
import numpy as np
import cv2
from ultralytics import YOLO
import torch
from ultralytics.nn.tasks import DetectionModel
from torch.nn import Sequential
import time
from datetime import datetime
import json
import uuid
import threading
from collections import deque
import math

load_dotenv()

os.environ['YOLO_VERBOSE'] = os.getenv('YOLO_VERBOSE', 'False')
os.environ['TORCH_WEIGHTS_ONLY'] = os.getenv('TORCH_WEIGHTS_ONLY', 'False')

# Permitir clases necesarias en PyTorch >=2.6
torch.serialization.add_safe_globals([DetectionModel, Sequential])

# Cargar modelo YOLO
model = YOLO(os.getenv('YOLO_MODEL_PATH', 'yolov8n.pt'))  # nano = más rápido, 's' = más preciso

# ================================
# CONFIGURACIÓN DE ENTRADA
# ================================
USE_VIDEO_FILE = os.getenv('USE_VIDEO_FILE', 'False') == 'True'  # True para video local, False para stream RTSP
VIDEO_FILE_PATH = os.getenv('VIDEO_FILE_PATH')
RTSP_URL = os.getenv('RTSP_URL_PATIO')

# ================================
# CONFIGURACIÓN DE GRABACIÓN
# ================================
RECORDING_DURATION = int(os.getenv('RECORDING_DURATION', 20)) # Segundos SIN detecciones para parar grabación
PRE_RECORDING_BUFFER = int(os.getenv('PRE_RECORDING_BUFFER', 3)) # Segundos de buffer antes de la detección
RECORDINGS_BASE_DIR = os.getenv('RECORDINGS_BASE_DIR', 'recordings')
RECORDING_FPS = int(os.getenv('RECORDING_FPS', 20)) # FPS para la grabación
SHOW_VIDEO_WINDOW = os.getenv("SHOW_VIDEO_WINDOW", "True").lower() == "true" # Mostrar ventana de video

# ================================
# CONFIGURACIÓN DE FILTRO DE OBJETOS ESTÁTICOS
# ================================
STATIC_OBJECT_TIMEOUT = float(os.getenv('STATIC_OBJECT_TIMEOUT', 30.0)) # Segundos para considerar un objeto como estático
POSITION_TOLERANCE = int(os.getenv('POSITION_TOLERANCE', 50)) # Píxeles de tolerancia para considerar misma posición
MIN_CONFIDENCE_FOR_TRACKING = float(os.getenv('MIN_CONFIDENCE_FOR_TRACKING', 0.4)) # Confianza mínima para iniciar seguimiento

# Diccionario para rastrear objetos detectados
tracked_objects = {}

# Dimensiones esperadas
width, height = 1280, 720
frame_size = width * height * 3

# Configurar device (GPU si está disponible)
device = 'cuda' if torch.cuda.is_available() else 'cpu'
model.to(device)
print(f"🚀 Usando dispositivo: {device}")

# Clases importantes para seguridad
security_classes = ['person', 'car', 'motorcycle', 'bicycle', 'bus', 'truck']

# Colores personalizados por clase
class_colors = {
    'person': (0, 255, 0),      # Verde para personas
    'bicycle': (255, 255, 0),   # Amarillo
    'car': (0, 0, 255),         # Rojo para vehículos
    'motorcycle': (255, 0, 255), # Magenta
    'bus': (0, 255, 255),       # Cian
    'truck': (128, 0, 128),     # Púrpura
    'bird': (0, 128, 255),
    'cat': (255, 128, 0),
    'dog': (128, 255, 0)
}

# Variables para optimización y grabación
frame_count = 0
start_time = time.time()
recording_active = False
current_recording_data = None
frame_buffer = deque(maxlen=PRE_RECORDING_BUFFER * RECORDING_FPS)
last_detection_time = None

# ================================
# FUNCIONES DE GRABACIÓN
# ================================
def create_recording_path():
    """Crear directorio de grabación basado en fecha actual"""
    now = datetime.now()
    year_dir = os.path.join(RECORDINGS_BASE_DIR, str(now.year))
    month_dir = os.path.join(year_dir, f"{now.month:02d}")
    day_dir = os.path.join(month_dir, f"{now.day:02d}")
    
    os.makedirs(day_dir, exist_ok=True)
    return day_dir

def generate_video_id():
    """Generar ID único de 8 caracteres"""
    return str(uuid.uuid4()).replace('-', '')[:8]

def calculate_distance(pos1, pos2):
    """Calcular distancia euclidiana entre dos posiciones"""
    return math.sqrt((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2)

def get_box_center(x1, y1, x2, y2):
    """Obtener el centro de una bounding box"""
    return ((x1 + x2) // 2, (y1 + y2) // 2)

def is_object_static(label, center_pos, confidence, current_time):
    """Verificar si un objeto debe considerarse estático"""
    object_key = f"{label}_{center_pos[0]}_{center_pos[1]}"
    
    # Buscar objetos similares en posiciones cercanas
    for existing_key, obj_data in tracked_objects.items():
        if existing_key.startswith(label + "_"):
            existing_pos = (obj_data['center_x'], obj_data['center_y'])
            distance = calculate_distance(center_pos, existing_pos)
            
            if distance <= POSITION_TOLERANCE:
                # Objeto encontrado en posición similar
                time_diff = current_time - obj_data['first_seen']
                obj_data['last_seen'] = current_time
                obj_data['confidence'] = max(obj_data['confidence'], confidence)
                
                if time_diff >= STATIC_OBJECT_TIMEOUT:
                    return True  # Objeto estático
                else:
                    return False  # Objeto aún en período de gracia
    
    # Nuevo objeto, agregarlo al seguimiento
    tracked_objects[object_key] = {
        'center_x': center_pos[0],
        'center_y': center_pos[1],
        'first_seen': current_time,
        'last_seen': current_time,
        'confidence': confidence,
        'class': label
    }
    
    return False  # Nuevo objeto, no es estático

def cleanup_old_objects(current_time, timeout=30):
    """Limpiar objetos que no se han visto en un tiempo"""
    keys_to_remove = []
    for key, obj_data in tracked_objects.items():
        if current_time - obj_data['last_seen'] > timeout:
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        del tracked_objects[key]

def start_recording(detection_info):
    """Iniciar grabación cuando se detecta algo"""
    global recording_active, current_recording_data, last_detection_time
    
    # Actualizar tiempo de última detección
    last_detection_time = time.time()
    
    if recording_active:
        # Si ya estamos grabando, solo añadir la detección
        current_recording_data['detections'].append(detection_info)
        return
    
    recording_active = True
    now = datetime.now()
    video_id = generate_video_id()
    
    # Crear información de grabación
    filename = f"motion_frente_{now.strftime('%Y%m%d')}_{video_id}.mp4"
    recording_dir = create_recording_path()
    full_path = os.path.join(recording_dir, filename)
    relative_path = os.path.relpath(full_path, RECORDINGS_BASE_DIR)
    
    current_recording_data = {
        'video_id': video_id,
        'filename': filename,
        'video_path': relative_path.replace('/', '\\'),  # Windows path format
        'full_path': full_path,
        'detections': [detection_info],
        'start_time': time.time(),
        'frames': list(frame_buffer),  # Copiar frames del buffer
        'writer': None
    }
    
    print(f"🎥 Iniciando grabación: {filename}")

def save_recording():
    """Guardar video y JSON en un hilo separado"""
    global current_recording_data
    
    if not current_recording_data:
        return
    
    def save_worker():
        data = current_recording_data.copy()
        frames = data['frames']
        
        try:
            # Configurar escritor de video
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(data['full_path'], fourcc, RECORDING_FPS, (width, height))
            
            # Escribir todos los frames
            for frame in frames:
                if frame is not None:
                    writer.write(frame)
            
            writer.release()
            
            # Crear JSON con información de la grabación
            json_data = {
                'video_id': data['video_id'],
                'filename': data['filename'],
                'video_path': data['video_path'],
                'detections': data['detections']
            }
            
            # Guardar JSON
            json_path = data['full_path'].replace('.mp4', '.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            
            print(f"✅ Grabación guardada: {data['filename']}")
            print(f"📊 Detecciones: {len(data['detections'])}")
            
        except Exception as e:
            print(f"❌ Error guardando grabación: {e}")
    
    # Ejecutar en hilo separado para no bloquear el video
    threading.Thread(target=save_worker, daemon=True).start()

# ================================
# CONFIGURACIÓN DE ENTRADA FLEXIBLE
# ================================
if USE_VIDEO_FILE:
    print(f"📹 Usando video local: {VIDEO_FILE_PATH}")
    cap = cv2.VideoCapture(VIDEO_FILE_PATH)
    if not cap.isOpened():
        print(f"❌ Error: No se pudo abrir el video {VIDEO_FILE_PATH}")
        exit()
    
    # Obtener dimensiones del video
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps_video = cap.get(cv2.CAP_PROP_FPS)
    
    print(f"📊 Video: {width}x{height}, {total_frames} frames, {fps_video:.1f} FPS")
    
    def get_frame():
        ret, frame = cap.read()
        if not ret:
            return None
        # Redimensionar si es necesario
        if frame.shape[:2] != (height, width):
            frame = cv2.resize(frame, (width, height))
        return frame
        
else:
    print(f"📡 Usando stream RTSP: {RTSP_URL}")
    # Inicia proceso FFmpeg
    process = (
        ffmpeg
        .input(RTSP_URL, rtsp_transport='tcp', rtsp_flags='prefer_tcp')
        .output('pipe:', format='rawvideo', pix_fmt='bgr24')
        .run_async(pipe_stdout=True, pipe_stderr=True)
    )
    
    def get_frame():
        in_bytes = process.stdout.read(frame_size)
        if not in_bytes:
            return None
        return np.frombuffer(in_bytes, np.uint8).reshape([height, width, 3]).copy()

print("✅ Modelo cargado y conexión a la fuente iniciada...")

def log_detection(label, confidence, timestamp):
    """Registrar detecciones importantes"""
    if label in security_classes and confidence > 0.5:
        print(f"🚨 ALERTA: {label} detectado con {confidence:.0%} de confianza a las {timestamp}")

# ================================
# BUCLE PRINCIPAL (IDÉNTICO PARA AMBAS FUENTES)
# ================================
while True:
    frame = get_frame()
    if frame is None:
        print("⚠️ No se recibió frame, saliendo...")
        break

    frame_count += 1
    # Limpiar objetos antiguos cada 100 frames
    if frame_count % 100 == 0:
        cleanup_old_objects(time.time())

    current_frame_time = (frame_count - 1) / RECORDING_FPS if USE_VIDEO_FILE else time.time() - start_time
    
    # Añadir frame al buffer circular
    frame_buffer.append(frame.copy())
    
    # Si estamos grabando, añadir frame a la grabación
    if recording_active and current_recording_data:
        current_recording_data['frames'].append(frame.copy())
        
        # Verificar si debemos parar la grabación (10 segundos después de la ÚLTIMA detección)
        if last_detection_time and time.time() - last_detection_time > RECORDING_DURATION:
            print("🛑 Finalizando grabación (10s sin detecciones)...")
            save_recording()
            recording_active = False
            current_recording_data = None
            last_detection_time = None
    
    # OPTIMIZACIÓN: Procesar cada N frames para mejor rendimiento
    # Procesar cada 4 frames
    if frame_count % 4 == 0:
        # Detectar objetos con YOLO
        results = model(frame, verbose=False, conf=0.4)[0]  # Confianza mínima 40%
        
        current_time = datetime.now().strftime("%H:%M:%S")
        detection_found = False
        
        for box in results.boxes:
            cls_id = int(box.cls[0])
            label = model.names[cls_id]
            conf = float(box.conf[0])

            # Filtrar solo clases importantes para seguridad
            if label not in security_classes and conf < 0.5:
                continue

            x1, y1, x2, y2 = map(int, box.xyxy[0])
            center_pos = get_box_center(x1, y1, x2, y2)
            
            # Verificar si es un objeto estático
            if conf >= MIN_CONFIDENCE_FOR_TRACKING:
                is_static = is_object_static(label, center_pos, conf, time.time())
                
                if is_static:
                    # Objeto estático - dibujar con color diferente pero no grabar
                    color = (128, 128, 128)  # Gris para objetos estáticos
                    thickness = 1
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
                    
                    text = f'{label} {conf:.0%} [STATIC]'
                    text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
                    cv2.rectangle(frame, (x1, y1-25), (x1 + text_size[0], y1), color, -1)
                    cv2.putText(frame, text, (x1, y1-8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                    continue  # No procesar como detección activa

            detection_found = True
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            color = class_colors.get(label, (255, 255, 255))

            # Dibujar rectángulo más grueso para objetos importantes
            thickness = 3 if label in security_classes else 2
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
            
            # Texto con fondo para mejor visibilidad
            text = f'{label} {conf:.0%}'
            text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
            cv2.rectangle(frame, (x1, y1-30), (x1 + text_size[0], y1), color, -1)
            cv2.putText(frame, text, (x1, y1-10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
            
            # Log de detecciones importantes
            log_detection(label, conf, current_time)
            
            # Crear información de detección para grabación
            detection_info = {
                'timestamp': current_frame_time,
                'class': label,
                'confidence': conf * 100  # Convertir a porcentaje
            }
            
            # Iniciar grabación si detectamos algo importante
            if label in security_classes and conf > 0.5:
                start_recording(detection_info)

    # Indicador visual de grabación
    if recording_active:
        cv2.circle(frame, (width - 30, 30), 15, (0, 0, 255), -1)  # Círculo rojo
        cv2.putText(frame, "REC", (width - 50, 40),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # Mostrar información de la fuente
    source_info = f"Video: {os.path.basename(VIDEO_FILE_PATH)}" if USE_VIDEO_FILE else "RTSP Stream"
    cv2.putText(frame, source_info, (10, height-50),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    # Mostrar información de objetos rastreados
    tracked_count = len([obj for obj in tracked_objects.values() 
                        if time.time() - obj['last_seen'] < 2])
    cv2.putText(frame, f"Tracked: {tracked_count}", (10, height-20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
           
    # Mostrar frame
    if SHOW_VIDEO_WINDOW:
        cv2.imshow("Cámara de Seguridad - YOLO", frame)

    # Para video: controlar velocidad de reproducción
    if USE_VIDEO_FILE:
        # Pausar entre frames para simular velocidad real del video
        time.sleep(1.0 / fps_video if fps_video > 0 else 0.033)

    if SHOW_VIDEO_WINDOW:
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("🛑 Salida solicitada por el usuario.")
            break

# ================================
# LIMPIEZA
# ================================
# Finalizar grabación activa si existe
if recording_active and current_recording_data:
    print("🔄 Finalizando grabación pendiente...")
    save_recording()

if USE_VIDEO_FILE:
    cap.release()
else:
    try:
        process.terminate()
        process.wait(timeout=5)
    except:
        process.kill()
    finally:
        if process.stdout:
            process.stdout.close()

cv2.destroyAllWindows()
print(f"📊 Procesados {frame_count} frames total")