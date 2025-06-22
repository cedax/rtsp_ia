from dotenv import load_dotenv
import os
import cv2
import torch
import numpy as np
from ultralytics import YOLO
from ultralytics.nn.tasks import DetectionModel
from torch.nn import Sequential
import threading
import time
import logging
from datetime import datetime
from collections import deque
import queue

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

# Configuración de YOLO desde .env
os.environ['YOLO_VERBOSE'] = os.getenv('YOLO_VERBOSE', 'False')
os.environ['TORCH_WEIGHTS_ONLY'] = os.getenv('TORCH_WEIGHTS_ONLY', 'False')

# Permitir clases necesarias en PyTorch
torch.serialization.add_safe_globals([DetectionModel, Sequential])

class YOLODetector:
    def __init__(self):
        self.model = None
        self.device = None
        self.detection_queue = queue.Queue(maxsize=5)  # Buffer para frames
        self.result_queue = queue.Queue(maxsize=10)    # Buffer para resultados
        self.processing_thread = None
        self.is_running = False
        
        # Configuración desde .env
        self.model_path = os.getenv('YOLO_MODEL_PATH', 'yolov8n.pt')
        self.min_confidence = float(os.getenv('MIN_CONFIDENCE_FOR_TRACKING', 0.4))
        
        # Clases importantes para seguridad
        self.security_classes = ['person', 'car', 'motorcycle', 'bicycle', 'bus', 'truck']
        
        # Colores personalizados por clase
        self.class_colors = {
            'person': (0, 255, 0),        # Verde
            'bicycle': (255, 255, 0),     # Amarillo
            'car': (0, 0, 255),           # Rojo
            'motorcycle': (255, 0, 255),  # Magenta
            'bus': (0, 255, 255),         # Cian
            'truck': (128, 0, 128),       # Púrpura
            'bird': (0, 128, 255),        # Azul claro
            'cat': (255, 128, 0),         # Naranja
            'dog': (128, 255, 0)          # Verde claro
        }
        
        # Inicializar modelo
        self._load_model()
        
    def _load_model(self):
        """Cargar modelo YOLO"""
        try:
            self.model = YOLO(self.model_path)
            
            # Configurar device (GPU si está disponible)
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
            self.model.to(self.device)
            
            logger.info(f"✅ Modelo YOLO cargado: {self.model_path}")
            logger.info(f"🚀 Usando dispositivo: {self.device}")
            
        except Exception as e:
            logger.error(f"❌ Error cargando modelo YOLO: {e}")
            raise
    
    def start_processing(self):
        """Iniciar hilo de procesamiento"""
        if not self.is_running:
            self.is_running = True
            self.processing_thread = threading.Thread(target=self._processing_worker, daemon=True)
            self.processing_thread.start()
            logger.info("🔄 Hilo de detección YOLO iniciado")
    
    def stop_processing(self):
        """Detener hilo de procesamiento"""
        self.is_running = False
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=2)
        logger.info("🛑 Hilo de detección YOLO detenido")
    
    def _processing_worker(self):
        """Worker que procesa frames en hilo separado"""
        while self.is_running:
            try:
                # Obtener frame del queue con timeout
                frame_data = self.detection_queue.get(timeout=0.1)
                
                if frame_data is None:
                    continue
                
                frame, frame_id = frame_data
                
                # Procesar con YOLO
                detections = self._detect_objects(frame)
                
                # Enviar resultado
                try:
                    self.result_queue.put((frame_id, detections), timeout=0.1)
                except queue.Full:
                    # Si el queue está lleno, descartar resultado más antiguo
                    try:
                        self.result_queue.get_nowait()
                        self.result_queue.put((frame_id, detections), timeout=0.1)
                    except queue.Empty:
                        pass
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"❌ Error en worker de detección: {e}")
    
    def _detect_objects(self, frame):
        """Detectar objetos en el frame"""
        try:
            # Ejecutar detección
            results = self.model(frame, verbose=False, conf=self.min_confidence)[0]
            
            detections = []
            current_time = datetime.now().strftime("%H:%M:%S")
            
            for box in results.boxes:
                cls_id = int(box.cls[0])
                label = self.model.names[cls_id]
                confidence = float(box.conf[0])
                
                # Coordenadas del bounding box
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                
                detection_info = {
                    'label': label,
                    'confidence': confidence,
                    'bbox': (x1, y1, x2, y2),
                    'timestamp': current_time,
                    'is_security_class': label in self.security_classes
                }
                
                detections.append(detection_info)
                
                # Log para clases importantes
                if label in self.security_classes and confidence > 0.5:
                    logger.info(f"🚨 DETECCIÓN: {label} ({confidence:.0%}) a las {current_time}")
            
            return detections
            
        except Exception as e:
            logger.error(f"❌ Error en detección YOLO: {e}")
            return []
    
    def add_frame_for_detection(self, frame, frame_id=None):
        """Añadir frame para detección asíncrona"""
        if frame_id is None:
            frame_id = int(time.time() * 1000)  # timestamp en ms
        
        try:
            # Intentar añadir al queue sin bloquear
            self.detection_queue.put((frame.copy(), frame_id), timeout=0.01)
            return True
        except queue.Full:
            # Si está lleno, descartar frame más antiguo
            try:
                self.detection_queue.get_nowait()
                self.detection_queue.put((frame.copy(), frame_id), timeout=0.01)
                return True
            except (queue.Empty, queue.Full):
                return False
    
    def get_latest_detections(self):
        """Obtener las detecciones más recientes"""
        latest_detections = None
        
        # Obtener todas las detecciones disponibles (solo la más reciente)
        while True:
            try:
                frame_id, detections = self.result_queue.get_nowait()
                latest_detections = (frame_id, detections)
            except queue.Empty:
                break
        
        return latest_detections
    
    def draw_detections(self, frame, detections):
        """Dibujar detecciones en el frame"""
        if not detections:
            return frame
        
        frame_with_detections = frame.copy()
        
        for detection in detections:
            label = detection['label']
            confidence = detection['confidence']
            x1, y1, x2, y2 = detection['bbox']
            is_security = detection['is_security_class']
            
            # Seleccionar color y grosor
            color = self.class_colors.get(label, (255, 255, 255))
            thickness = 3 if is_security else 2
            
            # Dibujar bounding box
            cv2.rectangle(frame_with_detections, (x1, y1), (x2, y2), color, thickness)
            
            # Preparar texto
            confidence_text = f'{confidence:.0%}'
            text = f'{label} {confidence_text}'
            
            # Calcular tamaño del texto
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.6
            text_thickness = 2
            text_size = cv2.getTextSize(text, font, font_scale, text_thickness)[0]
            
            # Dibujar fondo del texto
            text_bg_y1 = y1 - 30
            text_bg_y2 = y1
            text_bg_x1 = x1
            text_bg_x2 = x1 + text_size[0] + 10
            
            cv2.rectangle(frame_with_detections, 
                         (text_bg_x1, text_bg_y1), 
                         (text_bg_x2, text_bg_y2), 
                         color, -1)
            
            # Dibujar texto
            cv2.putText(frame_with_detections, text, 
                       (x1 + 5, y1 - 10),
                       font, font_scale, (0, 0, 0), text_thickness)
            
            # Añadir indicador especial para clases de seguridad
            if is_security and confidence > 0.7:
                # Dibujar círculo de alerta
                center_x = (x1 + x2) // 2
                cv2.circle(frame_with_detections, (center_x, y1 - 40), 8, (0, 0, 255), -1)
                cv2.putText(frame_with_detections, "!", 
                           (center_x - 4, y1 - 35),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        return frame_with_detections
    
    def process_frame_sync(self, frame):
        """Procesar frame de forma síncrona (para casos simples)"""
        detections = self._detect_objects(frame)
        return self.draw_detections(frame, detections), detections

# Función de conveniencia para usar desde el archivo principal
def create_detector():
    """Crear y retornar una instancia del detector"""
    return YOLODetector()

# Ejemplo de uso
if __name__ == "__main__":
    try:
        # Crear detector
        detector = YOLODetector()
        detector.start_processing()
        
        # Simular procesamiento de frames
        logger.info("🚀 Detector YOLO listo")
        logger.info("📝 Presiona Ctrl+C para terminar")
        
        frame_count = 0
        while True:
            # Aquí normalmente recibirías frames reales
            # Para el ejemplo, creamos un frame dummy
            dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
            
            # Enviar frame para detección asíncrona
            success = detector.add_frame_for_detection(dummy_frame, frame_count)
            
            if success:
                frame_count += 1
                logger.info(f"📷 Frame {frame_count} enviado para detección")
            
            # Obtener resultados
            result = detector.get_latest_detections()
            if result:
                frame_id, detections = result
                logger.info(f"✅ Detecciones para frame {frame_id}: {len(detections)} objetos")
            
            time.sleep(0.1)  # Simular 10 FPS
            
    except KeyboardInterrupt:
        logger.info("🛑 Interrupción por teclado")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
    finally:
        if 'detector' in locals():
            detector.stop_processing()
        logger.info("🏁 Detector terminado")