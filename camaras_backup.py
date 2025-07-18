import subprocess
import os
import threading
import time
import signal
import sys
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

class RTSPViewer:
    def __init__(self):
        self.ip = os.getenv('CAMERA_IP', '192.168.1.100')
        self.ports = [int(port.strip()) for port in os.getenv('CAMERA_PORTS', '554').split(',')]
        self.vps_mode = os.getenv('VPS_MODE', 'false').lower() == 'true'
        self.show_window = os.getenv('SHOW_WINDOW', 'true').lower() == 'true'
        self.rtsp_path = os.getenv('RTSP_PATH', '/cam/realmonitor?channel=1&subtype=0')
        self.username = os.getenv('RTSP_USERNAME', 'admin')
        self.password = os.getenv('RTSP_PASSWORD', 'admin')
        
        self.processes = []
        self.threads = []
        self.running = False
        
    def create_rtsp_url(self, port):
        """Crear URL RTSP con credenciales"""
        if self.username and self.password:
            return f"rtsp://{self.username}:{self.password}@{self.ip}:{port}{self.rtsp_path}"
        else:
            return f"rtsp://{self.ip}:{port}{self.rtsp_path}"
    
    def test_connection(self, port):
        """Probar conexión RTSP con FFmpeg"""
        rtsp_url = self.create_rtsp_url(port)
        print(f"Probando conexión a puerto {port}...")
        
        cmd = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'error',
            '-rtsp_transport', 'tcp',
            '-i', rtsp_url,
            '-frames:v', '1',
            '-f', 'null',
            '-'
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode == 0:
                print(f"✓ Conexión exitosa a puerto {port}")
                return True
            else:
                print(f"✗ Error en puerto {port}: {result.stderr.decode()}")
                return False
        except subprocess.TimeoutExpired:
            print(f"✗ Timeout en puerto {port}")
            return False
        except Exception as e:
            print(f"✗ Error en puerto {port}: {e}")
            return False
    
    def start_ffmpeg_display(self, port, camera_index):
        """Iniciar FFmpeg para mostrar video"""
        rtsp_url = self.create_rtsp_url(port)
        window_title = f"Camara_{camera_index + 1}_Puerto_{port}"
        
        if self.vps_mode or not self.show_window:
            # En modo VPS, solo procesar sin mostrar ventana
            cmd = [
                'ffmpeg',
                '-hide_banner',
                '-loglevel', 'warning',
                '-rtsp_transport', 'tcp',
                '-i', rtsp_url,
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-f', 'null',
                '-'
            ]
        else:
            # Mostrar ventana con FFplay
            cmd = [
                'ffplay',
                '-hide_banner',
                '-loglevel', 'warning',
                '-rtsp_transport', 'tcp',
                '-i', rtsp_url,
                '-window_title', window_title,
                '-x', '640',
                '-y', '480',
                '-fflags', 'nobuffer',
                '-flags', 'low_delay',
                '-framedrop'
            ]
        
        return cmd
    
    def camera_thread(self, port, camera_index):
        """Hilo para manejar cada cámara con FFmpeg"""
        while self.running:
            try:
                cmd = self.start_ffmpeg_display(port, camera_index)
                print(f"Iniciando cámara {camera_index + 1} en puerto {port}")
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if os.name != 'nt' else None
                )
                
                self.processes.append(process)
                
                # Esperar a que termine el proceso
                while self.running and process.poll() is None:
                    time.sleep(0.1)
                
                if process.poll() is None:
                    # Terminar proceso si aún está ejecutándose
                    if os.name != 'nt':
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    else:
                        process.terminate()
                    
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name != 'nt':
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        else:
                            process.kill()
                
                if process in self.processes:
                    self.processes.remove(process)
                    
            except Exception as e:
                print(f"Error en cámara {camera_index + 1}: {e}")
                
            if self.running:
                print(f"Reconectando cámara {camera_index + 1} en 5 segundos...")
                time.sleep(5)
    
    def start_streaming(self):
        """Iniciar streaming de todas las cámaras"""
        print(f"Iniciando RTSP Viewer con FFmpeg")
        print(f"Modo VPS: {self.vps_mode}")
        print(f"Mostrar ventanas: {self.show_window}")
        print(f"IP: {self.ip}")
        print(f"Puertos: {self.ports}")
        
        # Verificar que FFmpeg esté instalado
        if not self.check_ffmpeg():
            return
        
        # Probar conexiones
        valid_ports = []
        for port in self.ports:
            if self.test_connection(port):
                valid_ports.append(port)
        
        if not valid_ports:
            print("No se pudo conectar a ninguna cámara")
            return
        
        print(f"\nIniciando streaming en {len(valid_ports)} cámara(s)...")
        self.running = True
        
        # Crear hilos para cada cámara válida
        for i, port in enumerate(valid_ports):
            thread = threading.Thread(
                target=self.camera_thread,
                args=(port, i),
                daemon=True
            )
            thread.start()
            self.threads.append(thread)
            time.sleep(1)  # Esperar un poco entre conexiones
        
        try:
            if not self.vps_mode and self.show_window:
                print("Presiona Ctrl+C para salir")
                print("Cierra las ventanas de video para detener cámaras individuales")
            else:
                print("Modo VPS activado. Presiona Ctrl+C para salir")
            
            # Mantener el programa ejecutándose
            while self.running:
                time.sleep(1)
                # Verificar si todos los procesos han terminado
                if not self.processes:
                    print("Todos los procesos de cámara han terminado")
                    break
                    
        except KeyboardInterrupt:
            print("\nDeteniendo transmisión...")
            
        finally:
            self.stop_streaming()
    
    def stop_streaming(self):
        """Detener streaming y liberar recursos"""
        self.running = False
        
        # Terminar todos los procesos FFmpeg
        for process in self.processes.copy():
            try:
                if process.poll() is None:
                    if os.name != 'nt':
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    else:
                        process.terminate()
                    
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name != 'nt':
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        else:
                            process.kill()
            except Exception as e:
                print(f"Error terminando proceso: {e}")
        
        # Esperar a que terminen todos los hilos
        for thread in self.threads:
            thread.join(timeout=2)
        
        self.processes.clear()
        print("Transmisión detenida")
    
    def check_ffmpeg(self):
        """Verificar que FFmpeg esté instalado"""
        try:
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, 
                                  text=True)
            if result.returncode == 0:
                print("✓ FFmpeg encontrado")
                return True
        except FileNotFoundError:
            print("✗ FFmpeg no encontrado")
            print("Instala FFmpeg:")
            print("  Ubuntu/Debian: sudo apt update && sudo apt install ffmpeg")
            print("  CentOS/RHEL: sudo yum install ffmpeg")
            print("  macOS: brew install ffmpeg")
            return False
    
    def list_cameras(self):
        """Listar cámaras disponibles"""
        print("Escaneando cámaras disponibles...")
        available_cameras = []
        
        for port in self.ports:
            if self.test_connection(port):
                available_cameras.append(port)
        
        if available_cameras:
            print(f"Cámaras disponibles en puertos: {available_cameras}")
        else:
            print("No se encontraron cámaras disponibles")
        
        return available_cameras

def signal_handler(sig, frame):
    """Manejador de señales para salida limpia"""
    print("\nRecibida señal de terminación...")
    sys.exit(0)

def main():
    # Configurar manejador de señales
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    viewer = RTSPViewer()
    
    # Opción para solo listar cámaras
    if len(sys.argv) > 1 and sys.argv[1] == '--list':
        viewer.list_cameras()
        return
    
    viewer.start_streaming()

if __name__ == "__main__":
    main()