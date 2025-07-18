#!/data/data/com.termux/files/usr/bin/bash

CAM_PORT=554

PUERTOS_LOCALES=(8451 8453 8455 8457 8459 8461)

RANGO_IPS="192.169.0.0-100"

PID_FILE="/data/data/com.termux/files/usr/tmp/socat_pids.txt"

function escanear_camaras() {
  echo "🔍 Escaneando IPs activas con puerto $CAM_PORT abierto en $RANGO_IPS..."
  nmap -p $CAM_PORT --open $RANGO_IPS -oG - | awk '/554\/open/{print $2}'
}

function redirigir_a_camaras() {
  local index=0
  local ip
  > "$PID_FILE"

  for ip in $(escanear_camaras); do
    if [[ $index -ge ${#PUERTOS_LOCALES[@]} ]]; then
      echo "⚠️  Se encontraron más de ${#PUERTOS_LOCALES[@]} cámaras. Solo se redireccionan las primeras ${#PUERTOS_LOCALES[@]}."
      break
    fi

    local puerto_local=${PUERTOS_LOCALES[$index]}
    echo "🔁 Redirigiendo puerto local $puerto_local → $ip:$CAM_PORT"

    # Lanza socat en segundo plano
    nohup socat TCP-LISTEN:$puerto_local,fork TCP:$ip:$CAM_PORT >/dev/null 2>&1 &
    local pid=$!
    echo "$pid" >> "$PID_FILE"

    echo "✅ Cámara $ip redirigida en puerto local $puerto_local (PID $pid)"
    ((index++))
  done

  if [[ $index -eq 0 ]]; then
    echo "❌ No se encontraron cámaras en el rango $RANGO_IPS con el puerto $CAM_PORT abierto."
  fi
}

function matar_redirecciones() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "ℹ️  No hay archivo de PIDs guardado. Nada que matar."
    return
  fi

  echo "🛑 Matando procesos socat anteriores..."
  while read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "✔️  Proceso socat (PID $pid) detenido."
    fi
  done < "$PID_FILE"
  rm -f "$PID_FILE"
  echo "✅ Todos los procesos socat finalizados."
}

function menu() {
  while true; do
    echo ""
    echo "====== Redirección automática de cámaras RTSP ======"
    echo "1) Buscar cámaras y redirigir automáticamente (máx 5)"
    echo "2) Matar redirecciones socat activas"
    echo "3) Salir"
    read -rp "Elige una opción: " opt

    case "$opt" in
      1) redirigir_a_camaras ;;
      2) matar_redirecciones ;;
      3) exit 0 ;;
      *) echo "❌ Opción inválida, intenta de nuevo." ;;
    esac
    echo
  done
}

menu