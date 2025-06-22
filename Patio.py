from dotenv import load_dotenv
import os
import ffmpeg
import numpy as np
import cv2
import logging
import time
import sys
from yolo_detector import YOLODetector

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

# Variables globales
process = None
cap = None
detector = None

# ================================
# CONFIGURACIÓN DESDE .ENV
# ================================
USE_VIDEO_FILE = os.getenv('USE_VIDEO_FILE', 'False') == 'True'
VIDEO_FILE_PATH = os.getenv('VIDEO_FILE_PATH')
RTSP_URL = os.getenv('RTSP_URL_PATIO')
SHOW_VIDEO_WINDOW = os.getenv("SHOW_VIDEO_WINDOW", "True").lower() == "true"

# Dimensiones
width, height = 1280, 720
frame_size = width * height * 3

def initialize_detector():
    """Inicializar detector YOLO"""
    global detector
    try:
        detector = YOLODetector()
        detector.start_processing()
        logger.info("✅ Detector YOLO inicializado")
        return True
    except Exception as e:
        logger.error(f"❌ Error inicializando detector: {e}")
        return False

def cleanup_resources():
    """Limpiar todos los recursos"""
    global process, cap, detector
    
    logger.info("🔄 Limpiando recursos...")

    # Detener detector YOLO
    if detector:
        detector.stop_processing()
    
    # Cerrar ventanas si están abiertas
    if SHOW_VIDEO_WINDOW:
        cv2.destroyAllWindows()
    
    # Cerrar captura de video
    if cap:
        cap.release()
        cap = None
    
    # Cerrar proceso FFmpeg
    if process:
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            if process.poll() is not None:
                process.kill()
        finally:
            if hasattr(process, 'stdout') and process.stdout:
                process.stdout.close()
            process = None

def setup_video_source():
    """Configurar fuente de video según variables de entorno"""
    global cap, process
    
    try:
        if USE_VIDEO_FILE:
            # Configuración para archivo de video
            if not VIDEO_FILE_PATH or not os.path.exists(VIDEO_FILE_PATH):
                logger.error(f"❌ Video file no encontrado: {VIDEO_FILE_PATH}")
                return False
            
            logger.info(f"📹 Usando video local: {VIDEO_FILE_PATH}")
            cap = cv2.VideoCapture(VIDEO_FILE_PATH)
            
            if not cap.isOpened():
                logger.error(f"❌ Error: No se pudo abrir el video {VIDEO_FILE_PATH}")
                return False
            
            # Obtener información del video
            global width, height
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps_video = cap.get(cv2.CAP_PROP_FPS)
            
            logger.info(f"📊 Video: {width}x{height}, {total_frames} frames, {fps_video:.1f} FPS")
            return True
            
        else:
            # Configuración para stream RTSP
            if not RTSP_URL:
                logger.error("❌ RTSP_URL no configurada en .env")
                return False
            
            logger.info(f"📡 Usando stream RTSP: {RTSP_URL}")
            return connect_to_stream()
            
    except Exception as e:
        logger.error(f"❌ Error configurando fuente de video: {e}")
        return False

def connect_to_stream():
    """Conectar al stream RTSP"""
    global process
    
    try:
        # Configuración optimizada para FFmpeg
        process = (
            ffmpeg
            .input(RTSP_URL, 
                rtsp_transport='tcp', 
                rtsp_flags='prefer_tcp',
                analyzeduration=100000,
                probesize=100000,
                fflags='nobuffer',
                flags='low_delay',
                thread_queue_size=512)
            .output('pipe:', 
                format='rawvideo', 
                pix_fmt='bgr24',
                s=f'{width}x{height}',
                tune='zerolatency')
            .run_async(pipe_stdout=True, pipe_stderr=True, quiet=True)
        )
        
        logger.info("✅ Conexión al stream RTSP establecida")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error conectando al stream: {e}")
        return False

def get_frame():
    """Obtener frame según la fuente configurada"""
    global process, cap
    
    if USE_VIDEO_FILE:
        # Obtener frame de archivo de video
        if not cap or not cap.isOpened():
            logger.error("❌ Captura de video no disponible")
            return None
            
        ret, frame = cap.read()
        if not ret:
            logger.info("📹 Final del video alcanzado")
            return None
            
        # Redimensionar si es necesario
        if frame.shape[:2] != (height, width):
            frame = cv2.resize(frame, (width, height))
            
        return frame
        
    else:
        # Obtener frame de stream RTSP
        if not process or process.poll() is not None:
            logger.info("🔄 Reconectando stream...")
            if not connect_to_stream():
                return None
        
        try:
            in_bytes = process.stdout.read(frame_size)
            if len(in_bytes) != frame_size:
                logger.warning("⚠️ Frame incompleto recibido")
                return None
                
            frame = np.frombuffer(in_bytes, np.uint8).reshape([height, width, 3]).copy()
            return frame
            
        except Exception as e:
            logger.error(f"❌ Error leyendo frame: {e}")
            return None

def display_frame(frame, window_title="Video Stream"):
    """Mostrar frame con detecciones si está habilitado"""
    global detector
    
    processed_frame = frame
    
    # Si tenemos detector, procesar frame
    if detector:
        # OPCIÓN 1: Procesamiento asíncrono (recomendado para mejor rendimiento)
        # Enviar frame para detección en hilo separado
        detector.add_frame_for_detection(frame)
        
        # Obtener detecciones más recientes si están disponibles
        result = detector.get_latest_detections()
        if result:
            frame_id, detections = result
            processed_frame = detector.draw_detections(frame, detections)
        
        # OPCIÓN 2: Procesamiento síncrono (más simple pero puede ser más lento)
        # processed_frame, detections = detector.process_frame_sync(frame)
    
    # Mostrar frame solo si está habilitado
    if SHOW_VIDEO_WINDOW and processed_frame is not None:
        cv2.imshow(window_title, processed_frame)
        return cv2.waitKey(1) & 0xFF
    return -1

# Ejemplo de uso
if __name__ == "__main__":
    try:
        # Configurar fuente de video
        if not setup_video_source():
            logger.error("❌ No se pudo configurar la fuente de video")
            sys.exit(1)

        # Inicializar detector YOLO
        if not initialize_detector():
            logger.error("❌ No se pudo inicializar el detector")
            sys.exit(1)
        
        logger.info("🚀 Iniciando captura de frames...")
        logger.info(f"📺 Mostrar ventana: {'Sí' if SHOW_VIDEO_WINDOW else 'No'}")
        logger.info(f"📹 Fuente: {'Video local' if USE_VIDEO_FILE else 'Stream RTSP'}")
        
        frame_count = 0
        fps_video = None
        
        # Obtener FPS del video si es archivo local
        if USE_VIDEO_FILE and cap:
            fps_video = cap.get(cv2.CAP_PROP_FPS)
        
        while True:
            frame = get_frame()
            
            if frame is None:
                if USE_VIDEO_FILE:
                    logger.info("📹 Video terminado")
                    break
                else:
                    logger.warning("⚠️ No se recibió frame del stream")
                    time.sleep(0.1)
                    continue
            
            frame_count += 1
            
            # Mostrar información en el frame
            info_text = f"Frame: {frame_count} | Fuente: {'Video' if USE_VIDEO_FILE else 'RTSP'}"
            cv2.putText(frame, info_text, (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            
            # Mostrar frame SIN detecciones YOLO
            # key = display_frame(frame, "Cámara de Seguridad")

            # Mostrar frame CON detecciones YOLO
            key = display_frame(frame, "Cámara con Detección YOLO")
            
            # Salir con 'q'
            if key == ord('q'):
                logger.info("🛑 Salida solicitada por el usuario")
                break
            
            # Control de velocidad para video local
            if USE_VIDEO_FILE and fps_video and fps_video > 0:
                time.sleep(1.0 / fps_video)
            elif not USE_VIDEO_FILE:
                time.sleep(0.033)  # ~30 FPS para stream
                
    except KeyboardInterrupt:
        logger.info("🛑 Interrupción por teclado")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
    finally:
        cleanup_resources()
        logger.info("🏁 Aplicación terminada")