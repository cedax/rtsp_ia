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
import math

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

# Configuracion de YOLO desde .env
os.environ['YOLO_VERBOSE'] = os.getenv('YOLO_VERBOSE', 'False')
os.environ['TORCH_WEIGHTS_ONLY'] = os.getenv('TORCH_WEIGHTS_ONLY', 'False')

# Permitir clases necesarias en PyTorch
torch.serialization.add_safe_globals([DetectionModel, Sequential])

class TrackedObject:
    """Clase para rastrear objetos individuales"""
    def __init__(self, obj_id, label, bbox, confidence, timestamp):
        self.id = obj_id
        self.label = label
        self.bbox = bbox  # (x1, y1, x2, y2)
        self.confidence = confidence
        self.first_seen = timestamp
        self.last_seen = timestamp
        self.last_update = timestamp
        self.positions = deque(maxlen=30)  # Mas historial para mejor analisis
        self.positions.append((self.center_x, self.center_y, timestamp))
        self.is_static = False
        self.static_since = None
        self.movement_history = deque(maxlen=10)  # Historial de movimientos
        
    @property
    def center_x(self):
        """Centro X del objeto"""
        return (self.bbox[0] + self.bbox[2]) // 2
    
    @property
    def center_y(self):
        """Centro Y del objeto"""
        return (self.bbox[1] + self.bbox[3]) // 2
    
    def update_position(self, bbox, confidence, timestamp):
        """Actualizar posicion y estado del objeto"""
        self.bbox = bbox
        self.confidence = confidence
        self.last_seen = timestamp
        self.last_update = timestamp
        
        # Calcular movimiento desde la ultima posicion
        movement = 0
        if self.positions:
            last_pos = self.positions[-1]
            dx = self.center_x - last_pos[0]
            dy = self.center_y - last_pos[1]
            movement = math.sqrt(dx*dx + dy*dy)
        
        # Agregar nueva posicion al historial
        self.positions.append((self.center_x, self.center_y, timestamp))
        
        # Registrar movimiento en historial
        self.movement_history.append(movement)
        
        return movement
    
    def calculate_distance(self, other_bbox):
        """Calcular distancia euclidiana entre centros"""
        other_center_x = (other_bbox[0] + other_bbox[2]) // 2
        other_center_y = (other_bbox[1] + other_bbox[3]) // 2
        
        dx = self.center_x - other_center_x
        dy = self.center_y - other_center_y
        
        return math.sqrt(dx*dx + dy*dy)
    
    def calculate_area_overlap(self, other_bbox):
        """Calcular superposicion de areas entre bounding boxes (IoU)"""
        x1, y1, x2, y2 = self.bbox
        ox1, oy1, ox2, oy2 = other_bbox
        
        # Calcular interseccion
        inter_x1 = max(x1, ox1)
        inter_y1 = max(y1, oy1)
        inter_x2 = min(x2, ox2)
        inter_y2 = min(y2, oy2)
        
        if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
            return 0.0
        
        # Area de interseccion
        inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        
        # Area de cada bounding box
        area1 = (x2 - x1) * (y2 - y1)
        area2 = (ox2 - ox1) * (oy2 - oy1)
        
        # Union de areas
        union_area = area1 + area2 - inter_area
        
        if union_area == 0:
            return 0.0
            
        # IoU (Intersection over Union)
        return inter_area / union_area
    
    def check_if_static(self, static_timeout, position_tolerance):
        """Verificar si el objeto debe considerarse estatico - VERSION MEJORADA"""
        current_time = time.time()
        
        # Verificar que tengamos suficientes datos
        if len(self.positions) < 3:
            return False
        
        # El objeto debe existir al menos durante static_timeout para ser considerado estatico
        time_alive = current_time - self.first_seen
        if time_alive < static_timeout:
            return False
        
        # Analizar movimiento durante el periodo de timeout
        relevant_positions = []
        cutoff_time = current_time - static_timeout
        
        # Recopilar posiciones relevantes dentro del periodo de evaluacion
        for pos_x, pos_y, timestamp in self.positions:
            if timestamp >= cutoff_time:
                relevant_positions.append((pos_x, pos_y, timestamp))
        
        # Si no hay suficientes posiciones en el periodo, usar las mas recientes
        if len(relevant_positions) < 3:
            relevant_positions = list(self.positions)[-min(10, len(self.positions)):]
        
        # Calcular variacion de posicion
        if len(relevant_positions) < 2:
            return False
        
        # Calcular el rango de movimiento en X e Y
        x_positions = [pos[0] for pos in relevant_positions]
        y_positions = [pos[1] for pos in relevant_positions]
        
        x_range = max(x_positions) - min(x_positions)
        y_range = max(y_positions) - min(y_positions)
        
        # Calcular movimiento total maximo
        max_movement = math.sqrt(x_range**2 + y_range**2)
        
        # Calcular movimiento promedio reciente
        recent_movements = list(self.movement_history)[-5:] if self.movement_history else [0]
        avg_recent_movement = sum(recent_movements) / len(recent_movements)
        
        # Criterios para ser estatico:
        # 1. Movimiento maximo dentro de tolerancia
        # 2. Movimiento promedio reciente muy bajo
        # 3. Ha pasado suficiente tiempo
        
        is_position_stable = max_movement <= position_tolerance
        is_movement_low = avg_recent_movement <= (position_tolerance * 0.1)  # 10% de la tolerancia
        
        should_be_static = is_position_stable and is_movement_low
        
        # Actualizar estado
        if should_be_static and not self.is_static:
            self.is_static = True
            self.static_since = current_time
            logger.info(f"🔒 Objeto {self.label} (ID: {self.id}) marcado como ESTATICO despues de {time_alive:.1f}s (movimiento max: {max_movement:.1f}px, promedio: {avg_recent_movement:.1f}px)")
            return True
        elif not should_be_static and self.is_static:
            # Solo volver a activo si hay movimiento significativo
            logger.info(f"🔄 Objeto {self.label} (ID: {self.id}) ya NO es estatico (movimiento max: {max_movement:.1f}px, promedio: {avg_recent_movement:.1f}px)")

            if avg_recent_movement > (position_tolerance * 0.1):  # 10% de tolerancia para evitar fluctuaciones
                self.is_static = False
                self.static_since = None
                logger.info(f"🚶 Objeto {self.label} (ID: {self.id}) ya NO es estatico (movimiento: {avg_recent_movement:.1f}px)")
        
        return self.is_static
    
    def time_static(self):
        """Obtener tiempo que ha estado estatico en segundos"""
        if not self.is_static or self.static_since is None:
            return 0
        return time.time() - self.static_since

class YOLODetector:
    def __init__(self):
        self.model = None
        self.device = None
        self.detection_queue = queue.Queue(maxsize=5)  # Buffer para frames
        self.result_queue = queue.Queue(maxsize=10)    # Buffer para resultados
        self.processing_thread = None
        self.is_running = False
        
        # Configuracion desde .env
        self.model_path = os.getenv('YOLO_MODEL_PATH', 'yolov8n.pt')
        self.min_confidence = float(os.getenv('MIN_CONFIDENCE_FOR_TRACKING', 0.4))
        
        # Configuracion para objetos estaticos - VALORES MEJORADOS
        self.static_timeout = float(os.getenv('STATIC_OBJECT_TIMEOUT', 15.0))  # Reducido a 15s
        self.position_tolerance = float(os.getenv('POSITION_TOLERANCE', 30.0))  # Mas estricto: 30px
        
        # Habilitar debug para seguimiento
        self.debug_tracking = os.getenv('DEBUG_TRACKING', 'False').lower() == 'true'
        
        # Sistema de seguimiento - MEJORADO
        self.tracked_objects = {}  # obj_id -> TrackedObject
        self.next_object_id = 1
        self.max_tracking_distance = 500  # Distancia maxima mas estricta
        self.min_area_overlap = 0.4  # Aumentar superposicion minima
        
        # Limpieza de objetos antiguos
        self.object_cleanup_interval = 5.0  # segundos
        self.last_cleanup = time.time()
        
        # Clases importantes para seguridad
        self.security_classes = ['person', 'car', 'motorcycle', 'bicycle', 'bus', 'truck']
        
        # Colores personalizados por clase
        self.class_colors = {
            'person': (0, 255, 0),        # Verde
            'bicycle': (255, 255, 0),     # Amarillo
            'car': (0, 0, 255),           # Rojo
            'motorcycle': (255, 0, 255),  # Magenta
            'bus': (0, 255, 255),         # Cian
            'truck': (128, 0, 128),       # Purpura
            'bird': (0, 128, 255),        # Azul claro
            'cat': (255, 128, 0),         # Naranja
            'dog': (128, 255, 0)          # Verde claro
        }
        
        # Colores para estados
        self.static_color = (128, 128, 128)  # Gris para objetos estaticos
        self.moving_color = (0, 255, 0)      # Verde para objetos en movimiento
        
        # Inicializar modelo
        self._load_model()
        
        logger.info(f"⚡ Configuracion de seguimiento:")
        logger.info(f"   - Timeout para estatico: {self.static_timeout}s")
        logger.info(f"   - Tolerancia de posicion: {self.position_tolerance} pixeles")
        logger.info(f"   - Confianza minima: {self.min_confidence}")
        logger.info(f"   - Distancia maxima de seguimiento: {self.max_tracking_distance}px")
        logger.info(f"   - Superposicion minima: {self.min_area_overlap}")
        
    def _load_model(self):
        """Cargar modelo YOLO"""
        try:
            self.model = YOLO(self.model_path)
            
            # Configurar device (GPU si esta disponible)
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
            logger.info("🔄 Hilo de deteccion YOLO iniciado")
    
    def stop_processing(self):
        """Detener hilo de procesamiento"""
        self.is_running = False
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=2)
        logger.info("🛑 Hilo de deteccion YOLO detenido")
    
    def _processing_worker(self):
        """Worker que procesa frames en hilo separado"""
        while self.is_running:
            try:
                # Obtener frame del queue con timeout
                frame_data = self.detection_queue.get(timeout=0.1)
                
                if frame_data is None:
                    continue
                
                frame, frame_id = frame_data
                
                # Procesar con YOLO y rastrear objetos
                detections = self._detect_and_track_objects(frame)
                
                # Enviar resultado
                try:
                    self.result_queue.put((frame_id, detections), timeout=0.1)
                except queue.Full:
                    # Si el queue esta lleno, descartar resultado mas antiguo
                    try:
                        self.result_queue.get_nowait()
                        self.result_queue.put((frame_id, detections), timeout=0.1)
                    except queue.Empty:
                        pass
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"❌ Error en worker de deteccion: {e}")
    
    def _detect_and_track_objects(self, frame):
        """Detectar objetos y actualizar sistema de seguimiento - VERSION MEJORADA"""
        try:
            # Ejecutar deteccion
            results = self.model(frame, verbose=False, conf=self.min_confidence)[0]
            
            current_time = time.time()
            current_detections = []
            
            # Procesar detecciones nuevas
            for box in results.boxes:
                cls_id = int(box.cls[0])
                label = self.model.names[cls_id]
                confidence = float(box.conf[0])
                
                # Coordenadas del bounding box
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                bbox = (x1, y1, x2, y2)
                
                current_detections.append({
                    'label': label,
                    'confidence': confidence,
                    'bbox': bbox
                })
            
            # Actualizar seguimiento
            self._update_tracking_improved(current_detections, current_time)
            
            # Limpiar objetos antiguos periodicamente
            if current_time - self.last_cleanup > self.object_cleanup_interval:
                self._cleanup_old_objects(current_time)
                self.last_cleanup = current_time
            
            # Preparar detecciones para retornar
            tracked_detections = []
            timestamp_str = datetime.now().strftime("%H:%M:%S")
            
            for obj_id, tracked_obj in self.tracked_objects.items():
                # Verificar si es estatico
                is_static = tracked_obj.check_if_static(self.static_timeout, self.position_tolerance)
                
                detection_info = {
                    'object_id': obj_id,
                    'label': tracked_obj.label,
                    'confidence': tracked_obj.confidence,
                    'bbox': tracked_obj.bbox,
                    'timestamp': timestamp_str,
                    'is_security_class': tracked_obj.label in self.security_classes,
                    'is_static': is_static,
                    'time_static': tracked_obj.time_static(),
                    'first_seen': tracked_obj.first_seen,
                    'last_seen': tracked_obj.last_seen
                }
                
                tracked_detections.append(detection_info)
                
                # Log MENOS FRECUENTE para objetos activos
                if (not is_static and 
                    tracked_obj.label in self.security_classes and 
                    tracked_obj.confidence > 0.5 and
                    (current_time - tracked_obj.first_seen) % 5 < 0.1):  # Solo cada 5 segundos
                    logger.info(f"🚨 DETECCION ACTIVA: {tracked_obj.label} (ID: {obj_id}, {tracked_obj.confidence:.0%}) a las {timestamp_str}")
            
            return tracked_detections
            
        except Exception as e:
            logger.error(f"❌ Error en deteccion y seguimiento: {e}")
            return []
    
    def _update_tracking_improved(self, current_detections, current_time):
        """Version mejorada del sistema de seguimiento"""
        # Marcar todos los objetos como no actualizados
        for tracked_obj in self.tracked_objects.values():
            tracked_obj.last_update = tracked_obj.last_seen
        
        # Crear matriz de costos para asignacion optima
        cost_matrix = []
        object_ids = list(self.tracked_objects.keys())
        
        # Calcular costos para cada combinacion objeto-deteccion
        for obj_id in object_ids:
            tracked_obj = self.tracked_objects[obj_id]
            row_costs = []
            
            for detection in current_detections:
                if tracked_obj.label != detection['label']:
                    row_costs.append(float('inf'))  # Costo infinito para diferentes clases
                else:
                    # Calcular distancia
                    distance = tracked_obj.calculate_distance(detection['bbox'])
                    
                    # Calcular superposicion de area
                    overlap = tracked_obj.calculate_area_overlap(detection['bbox'])
                    
                    # Costo combinado (menor es mejor)
                    if distance > self.max_tracking_distance or overlap < self.min_area_overlap:
                        cost = float('inf')
                    else:
                        # Combinando distancia y falta de superposicion
                        cost = distance + (1.0 - overlap) * 100
                    
                    row_costs.append(cost)
            
            cost_matrix.append(row_costs)
        
        # Asignacion simple (greedy) - en un sistema mas avanzado usarias el algoritmo hungaro
        used_detections = set()
        used_objects = set()
        
        # Ordenar asignaciones por costo
        assignments = []
        for i, obj_id in enumerate(object_ids):
            for j, detection in enumerate(current_detections):
                if cost_matrix[i][j] != float('inf'):
                    assignments.append((cost_matrix[i][j], obj_id, j, detection))
        
        assignments.sort()  # Ordenar por costo
        
        # Realizar asignaciones
        for cost, obj_id, detection_idx, detection in assignments:
            if obj_id not in used_objects and detection_idx not in used_detections:
                # Asignar deteccion a objeto existente
                movement = self.tracked_objects[obj_id].update_position(
                    detection['bbox'], 
                    detection['confidence'], 
                    current_time
                )
                used_objects.add(obj_id)
                used_detections.add(detection_idx)
                
                if self.debug_tracking:
                    obj = self.tracked_objects[obj_id]
                    time_alive = current_time - obj.first_seen
                    logger.info(f"🔄 Objeto {obj.label} (ID: {obj_id}) actualizado - Movimiento: {movement:.1f}px, Tiempo vivo: {time_alive:.1f}s")
        
        # Crear nuevos objetos para detecciones no asignadas
        for i, detection in enumerate(current_detections):
            if i not in used_detections:
                new_obj = TrackedObject(
                    self.next_object_id,
                    detection['label'],
                    detection['bbox'],
                    detection['confidence'],
                    current_time
                )
                self.tracked_objects[self.next_object_id] = new_obj
                logger.info(f"🆕 Nuevo objeto detectado: {detection['label']} (ID: {self.next_object_id}) en posicion ({new_obj.center_x}, {new_obj.center_y})")
                self.next_object_id += 1
    
    def _cleanup_old_objects(self, current_time):
        """Eliminar objetos que no se han visto recientemente"""
        max_age = 3.0  # Reducido a 3 segundos para limpieza mas rapida
        objects_to_remove = []
        
        for obj_id, tracked_obj in self.tracked_objects.items():
            if current_time - tracked_obj.last_seen > max_age:
                objects_to_remove.append(obj_id)
        
        for obj_id in objects_to_remove:
            removed_obj = self.tracked_objects.pop(obj_id)
            time_alive = current_time - removed_obj.first_seen
            logger.info(f"🧹 Objeto eliminado: {removed_obj.label} (ID: {obj_id}) - Tiempo de vida: {time_alive:.1f}s, No visto por {max_age}s")
    
    def add_frame_for_detection(self, frame, frame_id=None):
        """Añadir frame para deteccion asincrona"""
        if frame_id is None:
            frame_id = int(time.time() * 1000)  # timestamp en ms
        
        try:
            # Intentar añadir al queue sin bloquear
            self.detection_queue.put((frame.copy(), frame_id), timeout=0.01)
            return True
        except queue.Full:
            # Si esta lleno, descartar frame mas antiguo
            try:
                self.detection_queue.get_nowait()
                self.detection_queue.put((frame.copy(), frame_id), timeout=0.01)
                return True
            except (queue.Empty, queue.Full):
                return False
    
    def get_latest_detections(self):
        """Obtener las detecciones mas recientes"""
        latest_detections = None
        
        # Obtener todas las detecciones disponibles (solo la mas reciente)
        while True:
            try:
                frame_id, detections = self.result_queue.get_nowait()
                latest_detections = (frame_id, detections)
            except queue.Empty:
                break
        
        return latest_detections
    
    def get_tracking_stats(self):
        """Obtener estadisticas del sistema de seguimiento"""
        total_objects = len(self.tracked_objects)
        static_objects = sum(1 for obj in self.tracked_objects.values() if obj.is_static)
        moving_objects = total_objects - static_objects
        
        return {
            'total_objects': total_objects,
            'static_objects': static_objects,
            'moving_objects': moving_objects,
            'next_id': self.next_object_id
        }
    
    def draw_detections(self, frame, detections):
        """Dibujar detecciones en el frame con informacion de seguimiento"""
        if not detections:
            return frame
        
        frame_with_detections = frame.copy()
        
        for detection in detections:
            label = detection['label']
            confidence = detection['confidence']
            x1, y1, x2, y2 = detection['bbox']
            is_security = detection['is_security_class']
            is_static = detection['is_static']
            obj_id = detection['object_id']
            time_static = detection['time_static']
            
            # Seleccionar color basado en estado
            if is_static:
                color = self.static_color
                status_text = f"ESTATICO ({time_static:.1f}s)"
            else:
                color = self.class_colors.get(label, (255, 255, 255))
                status_text = "ACTIVO"
            
            # Grosor basado en importancia y estado
            thickness = 1 if is_static else (3 if is_security else 2)
            
            # Dibujar bounding box
            cv2.rectangle(frame_with_detections, (x1, y1), (x2, y2), color, thickness)
            
            # Preparar texto
            confidence_text = f'{confidence:.0%}'
            main_text = f'{label} #{obj_id} {confidence_text}'
            
            # Calcular posiciones para texto
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.5
            text_thickness = 1
            
            # Linea 1: Label + ID + Confianza
            main_size = cv2.getTextSize(main_text, font, font_scale, text_thickness)[0]
            
            # Linea 2: Estado
            status_size = cv2.getTextSize(status_text, font, font_scale-0.1, text_thickness)[0]
            
            # Dibujar fondo del texto
            text_bg_height = 35
            text_bg_y1 = y1 - text_bg_height
            text_bg_y2 = y1
            text_bg_x1 = x1
            text_bg_x2 = x1 + max(main_size[0], status_size[0]) + 10
            
            cv2.rectangle(frame_with_detections, 
                         (text_bg_x1, text_bg_y1), 
                         (text_bg_x2, text_bg_y2), 
                         color, -1)
            
            # Dibujar textos
            cv2.putText(frame_with_detections, main_text, 
                       (x1 + 5, y1 - 20),
                       font, font_scale, (255, 255, 255), text_thickness)
            
            cv2.putText(frame_with_detections, status_text, 
                       (x1 + 5, y1 - 5),
                       font, font_scale-0.1, (255, 255, 255), text_thickness)
            
            # Añadir indicadores especiales
            if not is_static:
                if is_security and confidence > 0.5:
                    # Circulo de alerta para objetos de seguridad activos
                    center_x = (x1 + x2) // 2
                    cv2.circle(frame_with_detections, (center_x, y1 - 45), 8, (0, 0, 255), -1)
                    cv2.putText(frame_with_detections, "!", 
                               (center_x - 4, y1 - 40),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            else:
                # Simbolo para objetos estaticos
                center_x = (x1 + x2) // 2
                cv2.putText(frame_with_detections, "STATIC", 
                           (center_x - 25, y1 - 45),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (128, 128, 128), 1)
        
        # Mostrar estadisticas en la esquina
        stats = self.get_tracking_stats()
        stats_text = f"Objetos: {stats['total_objects']} | Activos: {stats['moving_objects']} | Estaticos: {stats['static_objects']}"
        cv2.putText(frame_with_detections, stats_text, 
                   (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        return frame_with_detections
    
    def process_frame_sync(self, frame):
        """Procesar frame de forma sincrona (para casos simples)"""
        detections = self._detect_and_track_objects(frame)
        return self.draw_detections(frame, detections), detections

# Funcion de conveniencia para usar desde el archivo principal
def create_detector():
    """Crear y retornar una instancia del detector"""
    return YOLODetector()

if __name__ == "__main__":
    try:
        # Crear detector
        detector = YOLODetector()
        detector.start_processing()
        
        # Simular procesamiento de frames
        logger.info("🚀 Detector YOLO con seguimiento mejorado listo")
        logger.info("📝 Presiona Ctrl+C para terminar")
        
        frame_count = 0
        while True:
            dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
            
            # Enviar frame para deteccion asincrona
            success = detector.add_frame_for_detection(dummy_frame, frame_count)
            
            if success:
                frame_count += 1
            
            # Obtener resultados
            result = detector.get_latest_detections()
            if result:
                frame_id, detections = result
                active_objects = [d for d in detections if not d['is_static']]
                static_objects = [d for d in detections if d['is_static']]
                
                if frame_count % 50 == 0:
                    stats = detector.get_tracking_stats()
                    logger.info(f"📊 Estadisticas: {stats}")
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        logger.info("🛑 Interrupcion por teclado")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
    finally:
        if 'detector' in locals():
            detector.stop_processing()
        logger.info("🏁 Detector terminado")