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
import signal
import sys
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

os.environ['YOLO_VERBOSE'] = os.getenv('YOLO_VERBOSE', 'False')
os.environ['TORCH_WEIGHTS_ONLY'] = os.getenv('TORCH_WEIGHTS_ONLY', 'False')

# Permitir clases necesarias en PyTorch >=2.6
torch.serialization.add_safe_globals([DetectionModel, Sequential])

# Variables globales para limpieza
process = None
cap = None
recording_active = False
current_recording_data = None
shutdown_requested = False

def signal_handler(sig, frame):
    """Manejador de señales para limpieza segura"""
    global shutdown_requested
    logger.info("🛑 Señal de terminación recibida, cerrando aplicación...")
    shutdown_requested = True

# Registrar manejador de señales
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

try:
    # Cargar modelo YOLO
    model = YOLO(os.getenv('YOLO_MODEL_PATH', 'yolov8n.pt'))
    logger.info("✅ Modelo YOLO cargado correctamente")
except Exception as e:
    logger.error(f"❌ Error cargando modelo YOLO: {e}")
    sys.exit(1)

# ================================
# CONFIGURACIÓN DE ENTRADA
# ================================
USE_VIDEO_FILE = os.getenv('USE_VIDEO_FILE', 'False') == 'True'
VIDEO_FILE_PATH = os.getenv('VIDEO_FILE_PATH')
RTSP_URL = os.getenv('RTSP_URL_FRENTE')

# ================================
# CONFIGURACIÓN DE GRABACIÓN
# ================================
RECORDING_DURATION = int(os.getenv('RECORDING_DURATION', 20))
PRE_RECORDING_BUFFER = int(os.getenv('PRE_RECORDING_BUFFER', 3))
RECORDINGS_BASE_DIR = os.getenv('RECORDINGS_BASE_DIR', 'recordings')
RECORDING_FPS = int(os.getenv('RECORDING_FPS', 20))
SHOW_VIDEO_WINDOW = os.getenv("SHOW_VIDEO_WINDOW", "True").lower() == "true"

# ================================
# CONFIGURACIÓN DE FILTRO DE OBJETOS ESTÁTICOS
# ================================
STATIC_OBJECT_TIMEOUT = float(os.getenv('STATIC_OBJECT_TIMEOUT', 30.0))
POSITION_TOLERANCE = int(os.getenv('POSITION_TOLERANCE', 50))
MIN_CONFIDENCE_FOR_TRACKING = float(os.getenv('MIN_CONFIDENCE_FOR_TRACKING', 0.4))

# Diccionario para rastrear objetos detectados con lock para thread safety
tracked_objects = {}
tracked_objects_lock = threading.Lock()

# Dimensiones esperadas
width, height = 1280, 720
frame_size = width * height * 3

# Configurar device (GPU si está disponible)
device = 'cuda' if torch.cuda.is_available() else 'cpu'
model.to(device)
logger.info(f"🚀 Usando dispositivo: {device}")

# Clases importantes para seguridad
security_classes = ['person', 'car', 'motorcycle', 'bicycle', 'bus', 'truck']

# Colores personalizados por clase
class_colors = {
    'person': (0, 255, 0),
    'bicycle': (255, 255, 0),
    'car': (0, 0, 255),
    'motorcycle': (255, 0, 255),
    'bus': (0, 255, 255),
    'truck': (128, 0, 128),
    'bird': (0, 128, 255),
    'cat': (255, 128, 0),
    'dog': (128, 255, 0)
}

# Variables para optimización y grabación
frame_count = 0
start_time = time.time()
frame_buffer = deque(maxlen=PRE_RECORDING_BUFFER * RECORDING_FPS)
last_detection_time = None

# Lock para thread safety en grabación
recording_lock = threading.Lock()

# ================================
# FUNCIONES DE GRABACIÓN
# ================================
def create_recording_path():
    """Crear directorio de grabación basado en fecha actual"""
    try:
        now = datetime.now()
        year_dir = os.path.join(RECORDINGS_BASE_DIR, str(now.year))
        month_dir = os.path.join(year_dir, f"{now.month:02d}")
        day_dir = os.path.join(month_dir, f"{now.day:02d}")
        
        os.makedirs(day_dir, exist_ok=True)
        return day_dir
    except Exception as e:
        logger.error(f"Error creando directorio de grabación: {e}")
        return RECORDINGS_BASE_DIR

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
    with tracked_objects_lock:
        object_key = f"{label}_{center_pos[0]}_{center_pos[1]}"
        
        # Buscar objetos similares en posiciones cercanas
        for existing_key, obj_data in tracked_objects.items():
            if existing_key.startswith(label + "_"):
                existing_pos = (obj_data['center_x'], obj_data['center_y'])
                distance = calculate_distance(center_pos, existing_pos)
                
                if distance <= POSITION_TOLERANCE:
                    time_diff = current_time - obj_data['first_seen']
                    obj_data['last_seen'] = current_time
                    obj_data['confidence'] = max(obj_data['confidence'], confidence)
                    
                    return time_diff >= STATIC_OBJECT_TIMEOUT
        
        # Nuevo objeto
        tracked_objects[object_key] = {
            'center_x': center_pos[0],
            'center_y': center_pos[1],
            'first_seen': current_time,
            'last_seen': current_time,
            'confidence': confidence,
            'class': label
        }
        
        return False

def cleanup_old_objects(current_time, timeout=30):
    """Limpiar objetos que no se han visto en un tiempo"""
    with tracked_objects_lock:
        keys_to_remove = [
            key for key, obj_data in tracked_objects.items()
            if current_time - obj_data['last_seen'] > timeout
        ]
        
        for key in keys_to_remove:
            del tracked_objects[key]

def start_recording(detection_info):
    """Iniciar grabación cuando se detecta algo"""
    global recording_active, current_recording_data, last_detection_time
    
    with recording_lock:
        last_detection_time = time.time()
        
        if recording_active:
            if current_recording_data:
                current_recording_data['detections'].append(detection_info)
            return
        
        recording_active = True
        now = datetime.now()
        video_id = generate_video_id()
        
        filename = f"motion_frente_{now.strftime('%Y%m%d')}_{video_id}.mp4"
        recording_dir = create_recording_path()
        full_path = os.path.join(recording_dir, filename)
        relative_path = os.path.relpath(full_path, RECORDINGS_BASE_DIR)
        
        # Crear copia del buffer para evitar problemas de concurrencia
        buffer_copy = list(frame_buffer)
        
        current_recording_data = {
            'video_id': video_id,
            'filename': filename,
            'video_path': relative_path.replace('\\', '/'),  # Unix path format
            'full_path': full_path,
            'detections': [detection_info],
            'start_time': time.time(),
            'frames': buffer_copy,
            'writer': None,
            'frames_lock': threading.Lock()
        }
        
        logger.info(f"🎥 Iniciando grabación: {filename}")

def save_recording():
    """Guardar video y JSON en un hilo separado"""
    global current_recording_data
    
    if not current_recording_data:
        return
    
    def save_worker():
        data = None
        with recording_lock:
            if current_recording_data:
                data = current_recording_data.copy()
                # Hacer copia profunda de los frames
                data['frames'] = current_recording_data['frames'].copy()
        
        if not data:
            return
        
        writer = None
        try:
            frames = data['frames']
            if not frames:
                logger.warning("No hay frames para guardar")
                return
            
            # Crear el directorio si no existe
            os.makedirs(os.path.dirname(data['full_path']), exist_ok=True)
            
            # Configurar escritor de video con configuración más robusta
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(
                data['full_path'], 
                fourcc, 
                RECORDING_FPS, 
                (width, height),
                True  # isColor
            )
            
            if not writer.isOpened():
                logger.error("No se pudo abrir el escritor de video")
                return
            
            # Escribir frames con validación
            frames_written = 0
            for frame in frames:
                if frame is not None and frame.shape == (height, width, 3):
                    writer.write(frame)
                    frames_written += 1
            
            logger.info(f"Frames escritos: {frames_written}")
            
        except Exception as e:
            logger.error(f"❌ Error escribiendo video: {e}")
            return
        finally:
            if writer:
                writer.release()
        
        try:
            # Crear y guardar JSON
            json_data = {
                'video_id': data['video_id'],
                'filename': data['filename'],
                'video_path': data['video_path'],
                'timestamp': datetime.now().isoformat(),
                'detections': data['detections']
            }
            
            json_path = data['full_path'].replace('.mp4', '.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"✅ Grabación guardada: {data['filename']}")
            logger.info(f"📊 Detecciones: {len(data['detections'])}")
            
        except Exception as e:
            logger.error(f"❌ Error guardando JSON: {e}")
    
    # Ejecutar en hilo separado
    threading.Thread(target=save_worker, daemon=True).start()

def cleanup_resources():
    """Limpiar recursos al cerrar"""
    global process, cap, recording_active, current_recording_data
    
    logger.info("🔄 Limpiando recursos...")
    
    # Finalizar grabación activa
    if recording_active and current_recording_data:
        logger.info("🔄 Finalizando grabación pendiente...")
        save_recording()
        time.sleep(2)  # Dar tiempo para que se guarde
    
    # Cerrar ventanas
    if SHOW_VIDEO_WINDOW:
        cv2.destroyAllWindows()
    
    # Cerrar captura de video
    if cap:
        cap.release()
    
    # Cerrar proceso FFmpeg
    if process:
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            if process.poll() is None:
                process.kill()
        finally:
            if hasattr(process, 'stdout') and process.stdout:
                process.stdout.close()

# ================================
# CONFIGURACIÓN DE ENTRADA FLEXIBLE
# ================================
try:
    if USE_VIDEO_FILE:
        if not VIDEO_FILE_PATH or not os.path.exists(VIDEO_FILE_PATH):
            logger.error(f"❌ Video file no encontrado: {VIDEO_FILE_PATH}")
            sys.exit(1)
        
        logger.info(f"📹 Usando video local: {VIDEO_FILE_PATH}")
        cap = cv2.VideoCapture(VIDEO_FILE_PATH)
        
        if not cap.isOpened():
            logger.error(f"❌ Error: No se pudo abrir el video {VIDEO_FILE_PATH}")
            sys.exit(1)
        
        # Obtener dimensiones del video
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps_video = cap.get(cv2.CAP_PROP_FPS)
        
        logger.info(f"📊 Video: {width}x{height}, {total_frames} frames, {fps_video:.1f} FPS")
        
        def get_frame():
            if not cap or not cap.isOpened():
                return None
            ret, frame = cap.read()
            if not ret:
                return None
            if frame.shape[:2] != (height, width):
                frame = cv2.resize(frame, (width, height))
            return frame
            
    else:
        if not RTSP_URL:
            logger.error("❌ RTSP_URL no configurada")
            sys.exit(1)
        
        logger.info(f"📡 Usando stream RTSP: {RTSP_URL}")
        
        # Configuración más robusta para FFmpeg
        process = (
            ffmpeg
            .input(RTSP_URL, 
                   rtsp_transport='tcp', 
                   rtsp_flags='prefer_tcp',
                   analyzeduration=1000000,
                   probesize=1000000)
            .output('pipe:', 
                   format='rawvideo', 
                   pix_fmt='bgr24',
                   s=f'{width}x{height}')
            .run_async(pipe_stdout=True, pipe_stderr=True, quiet=True)
        )
        
        def get_frame():
            if not process or process.poll() is not None:
                return None
            try:
                in_bytes = process.stdout.read(frame_size)
                if len(in_bytes) != frame_size:
                    return None
                return np.frombuffer(in_bytes, np.uint8).reshape([height, width, 3]).copy()
            except Exception as e:
                logger.error(f"Error leyendo frame: {e}")
                return None

except Exception as e:
    logger.error(f"❌ Error configurando entrada: {e}")
    sys.exit(1)

logger.info("✅ Modelo cargado y conexión a la fuente iniciada...")

def log_detection(label, confidence, timestamp):
    """Registrar detecciones importantes"""
    if label in security_classes and confidence > 0.5:
        logger.info(f"🚨 ALERTA: {label} detectado con {confidence:.0%} de confianza a las {timestamp}")

# ================================
# BUCLE PRINCIPAL
# ================================
try:
    while not shutdown_requested:
        frame = get_frame()
        if frame is None:
            logger.warning("⚠️ No se recibió frame")
            if USE_VIDEO_FILE:
                break  # Final del video
            else:
                time.sleep(0.1)
                continue

        frame_count += 1
        
        # Limpiar objetos antiguos cada 100 frames
        if frame_count % 100 == 0:
            cleanup_old_objects(time.time())

        current_frame_time = (frame_count - 1) / RECORDING_FPS if USE_VIDEO_FILE else time.time() - start_time
        
        # Añadir frame al buffer circular
        frame_buffer.append(frame.copy())
        
        # Manejo de grabación con thread safety
        with recording_lock:
            if recording_active and current_recording_data:
                with current_recording_data.get('frames_lock', threading.Lock()):
                    current_recording_data['frames'].append(frame.copy())
                
                # Verificar si debemos parar la grabación
                if last_detection_time and time.time() - last_detection_time > RECORDING_DURATION:
                    logger.info(f"🛑 Finalizando grabación después de {RECORDING_DURATION}s sin detecciones")
                    save_recording()
                    recording_active = False
                    current_recording_data = None
                    last_detection_time = None
        
        # Procesar cada 4 frames para mejor rendimiento
        if frame_count % 4 == 0:
            try:
                results = model(frame, verbose=False, conf=0.4)[0]
                
                current_time = datetime.now().strftime("%H:%M:%S")
                detection_found = False
                
                for box in results.boxes:
                    cls_id = int(box.cls[0])
                    label = model.names[cls_id]
                    conf = float(box.conf[0])

                    # Filtrar solo clases importantes
                    if label not in security_classes and conf < 0.5:
                        continue

                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    center_pos = get_box_center(x1, y1, x2, y2)
                    
                    # Verificar si es objeto estático
                    if conf >= MIN_CONFIDENCE_FOR_TRACKING:
                        is_static = is_object_static(label, center_pos, conf, time.time())
                        
                        if is_static:
                            # Objeto estático - dibujar en gris
                            color = (128, 128, 128)
                            thickness = 1
                            cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
                            
                            text = f'{label} {conf:.0%} [STATIC]'
                            text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
                            cv2.rectangle(frame, (x1, y1-25), (x1 + text_size[0], y1), color, -1)
                            cv2.putText(frame, text, (x1, y1-8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                            continue

                    detection_found = True
                    color = class_colors.get(label, (255, 255, 255))
                    thickness = 3 if label in security_classes else 2
                    
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
                    
                    text = f'{label} {conf:.0%}'
                    text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
                    cv2.rectangle(frame, (x1, y1-30), (x1 + text_size[0], y1), color, -1)
                    cv2.putText(frame, text, (x1, y1-10),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
                    
                    log_detection(label, conf, current_time)
                    
                    detection_info = {
                        'timestamp': current_frame_time,
                        'class': label,
                        'confidence': conf * 100
                    }
                    
                    if label in security_classes and conf > 0.5:
                        start_recording(detection_info)
                        
            except Exception as e:
                logger.error(f"Error en detección: {e}")

        # Indicador visual de grabación
        if recording_active:
            cv2.circle(frame, (width - 30, 30), 15, (0, 0, 255), -1)
            cv2.putText(frame, "REC", (width - 50, 40),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # Información de estado
        source_info = f"Video: {os.path.basename(VIDEO_FILE_PATH)}" if USE_VIDEO_FILE else "RTSP Stream"
        cv2.putText(frame, source_info, (10, height-50),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        with tracked_objects_lock:
            tracked_count = len([obj for obj in tracked_objects.values() 
                               if time.time() - obj['last_seen'] < 2])
        cv2.putText(frame, f"Tracked: {tracked_count}", (10, height-20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
               
        # Mostrar frame
        if SHOW_VIDEO_WINDOW:
            cv2.imshow("Cámara de Seguridad - YOLO", frame)

        # Control de velocidad para video
        if USE_VIDEO_FILE:
            time.sleep(1.0 / fps_video if fps_video > 0 else 0.033)

        if SHOW_VIDEO_WINDOW:
            if cv2.waitKey(1) & 0xFF == ord('q'):
                logger.info("🛑 Salida solicitada por el usuario.")
                break

except KeyboardInterrupt:
    logger.info("🛑 Interrupción por teclado")
except Exception as e:
    logger.error(f"❌ Error en bucle principal: {e}")
finally:
    cleanup_resources()

logger.info(f"📊 Procesados {frame_count} frames total")
logger.info("🏁 Aplicación terminada correctamente")