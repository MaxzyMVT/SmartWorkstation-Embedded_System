import React, { useState, useEffect } from 'react';
import { 
  Armchair, 
  User, 
  Sun, 
  Thermometer, 
  Volume2, 
  Clock, 
  CheckCircle2, 
  AlertTriangle, 
  Cpu, 
  Activity,
  Lock,
  CircuitBoard,
  Zap,
  Server,
  Layers,
  Radio,
  Wifi,
  WifiOff
} from 'lucide-react';
import { subscribeToWorkstationTelemetry } from './telemetryService';

const RFC_STATES = {
  0: { name: 'Optimal Study', advice: 'Posture and study environment are optimal', type: 'optimal' },
  1: { name: 'Low Light (<300 Lux)', advice: 'Workspace too dim! Boosting LED lamp to 100%', type: 'alert' },
  2: { name: 'Harsh Glare (>750 Lux)', advice: 'Harsh overhead glare! Lowering curtain glare shield to 40%', type: 'alert' },
  3: { name: 'Text Neck (Too Close)', advice: 'Head too close (<28cm)! Straighten neck and sit back', type: 'alert' },
  4: { name: 'Slouching (Too Far)', advice: 'Slouching too far back (>65cm)! Align spine upright', type: 'alert' },
  5: { name: 'Break Timer Reached', advice: '50-minute study interval reached! Stand up and rest your eyes', type: 'alert' },
  6: { name: 'Workstation Vacant (Away)', advice: 'Workstation vacant. Power saving active', type: 'idle' },
  7: { name: 'Noisy Environment (>68 dB)', advice: 'High acoustic disturbance! Lowering curtain for acoustic shielding', type: 'alert' },
  8: { name: 'Thermal Discomfort', advice: 'Ambient temperature outside comfort range (<20°C or >32°C)! Curtain deployed', type: 'alert' },
};

export default function App() {
  const [connectionStatus, setConnectionStatus] = useState('Connecting...');
  const [lastGatewayRx, setLastGatewayRx] = useState(Date.now());
  const [lastNode1Rx, setLastNode1Rx] = useState(null);
  const [lastNode2Rx, setLastNode2Rx] = useState(null);
  const [currentTime, setCurrentTime] = useState(Date.now());

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const [telemetry, setTelemetry] = useState({
    seat: true,
    distance: 42.5,
    light: 450.0,
    temp: 25.6,
    sound: 35.0,
    sitting_duration_min: 14.5,
    curtain_state: 'OPEN',
    led_duty_cycle: 65.0,
    servo_angle: 85.0,
    break_active: false,
    break_remaining_s: 0,
    ai_state: {
      class_id: 0,
      class_name: 'OPTIMAL_STUDY',
      confidence: 0.96,
      latency_ms: 0.45
    },
    node_status: {
      node1: 'ONLINE',
      node2: 'ONLINE'
    },
    timestamp: Date.now() / 1000,
    _source: 'init'
  });

  useEffect(() => {
    const unsubscribe = subscribeToWorkstationTelemetry(
      (data) => {
        if (data._type === 'node1_heartbeat') {
          setLastNode1Rx(data._rx_node1 || Date.now());
          return;
        }
        if (data._type === 'node2_heartbeat') {
          setLastNode2Rx(data._rx_node2 || Date.now());
          return;
        }

        setLastGatewayRx(Date.now());
        setTelemetry((prev) => ({
          ...prev,
          seat: data.seat !== undefined ? Boolean(data.seat) : prev.seat,
          distance: data.distance !== undefined ? Number(data.distance) : prev.distance,
          light: data.light !== undefined ? Number(data.light) : prev.light,
          temp: data.temp !== undefined ? Number(data.temp) : prev.temp,
          sound: data.sound !== undefined ? Number(data.sound) : prev.sound,
          sitting_duration_min: data.sitting_duration_min !== undefined 
            ? Number(data.sitting_duration_min) 
            : prev.sitting_duration_min,
          curtain_state: data.curtain_state || prev.curtain_state,
          led_duty_cycle: data.led_duty_cycle !== undefined ? Number(data.led_duty_cycle) : prev.led_duty_cycle,
          servo_angle: data.servo_angle !== undefined ? Number(data.servo_angle) : prev.servo_angle,
          break_active: data.break_active !== undefined ? Boolean(data.break_active) : prev.break_active,
          break_remaining_s: data.break_remaining_s !== undefined ? Number(data.break_remaining_s) : prev.break_remaining_s,
          ai_state: data.ai_state || prev.ai_state,
          node_status: data.node_status || prev.node_status,
          timestamp: data.timestamp || Date.now() / 1000,
          _source: data._source || prev._source
        }));
      },
      (status) => {
        setConnectionStatus(status);
      }
    );

    return () => unsubscribe();
  }, []);

  const activeClassId = telemetry.ai_state?.class_id ?? 0;
  const currentRfc = RFC_STATES[activeClassId] || {
    name: telemetry.ai_state?.class_name || 'Autonomous AI Active',
    advice: telemetry.ai_state?.advice || 'Closed-loop intelligence managing workstation environment',
    type: activeClassId === 0 ? 'optimal' : activeClassId === 6 ? 'idle' : 'alert'
  };

  const adviceText = telemetry.ai_state?.advice || currentRfc.advice;
  const isAlert = currentRfc.type === 'alert';
  const isIdle = currentRfc.type === 'idle' || !telemetry.seat;

  const bannerBg = isAlert 
    ? 'bg-[#FFF0F2] border-[#FFCBD3]' 
    : isIdle 
    ? 'bg-slate-50 border-slate-200' 
    : 'bg-[#E5FDEB] border-green-200';

  const iconBoxStyle = isAlert
    ? 'bg-white text-rose-500 border-rose-200'
    : isIdle
    ? 'bg-white text-slate-500 border-slate-200'
    : 'bg-white text-emerald-600 border-emerald-200';

  const badgeStyle = isAlert
    ? 'bg-rose-100 text-rose-700'
    : isIdle
    ? 'bg-slate-200 text-slate-700'
    : 'bg-emerald-100 text-emerald-800';

  // Format study session duration
  const sittingMin = telemetry.sitting_duration_min || 0;
  const hrs = Math.floor(sittingMin / 60);
  const mins = Math.floor(sittingMin % 60);
  const formattedSessionTime = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m ${Math.floor((sittingMin % 1) * 60)}s`;

  // Gateway connectivity
  const gatewayAgeSec = Math.max(0, (currentTime - lastGatewayRx) / 1000);
  const isGatewayOnline = gatewayAgeSec <= 4.0;
  const gatewayStatus = isGatewayOnline ? 'ONLINE' : gatewayAgeSec <= 15.0 ? 'LAGGING' : 'OFFLINE';
  const gatewayBadgeText = isGatewayOnline ? 'ONLINE' : gatewayAgeSec <= 15.0 ? 'LAG' : 'OFFLINE';
  const gatewaySubtext = isGatewayOnline ? `${gatewayAgeSec.toFixed(1)}s heartbeat` : 'No signal';

  // Node 1 (Chair) connectivity
  const node1DirectAgeSec = lastNode1Rx ? Math.max(0, (currentTime - lastNode1Rx) / 1000) : null;
  const node1GatewayStatus = telemetry.node_status?.node1;
  let node1Status = 'STANDBY';
  let node1BadgeText = 'STANDBY';
  let node1Subtext = 'Awaiting signal';
  let node1Desc = 'FSR402 Seat Mat';
  let node1Theme = 'gray';

  if ((node1DirectAgeSec !== null && node1DirectAgeSec <= 20) || (isGatewayOnline && node1GatewayStatus === 'ONLINE')) {
    node1Status = 'ONLINE';
    node1BadgeText = 'ONLINE';
    node1Subtext = telemetry.seat ? 'Seated' : 'Ready';
    node1Desc = `FSR402 Mat • ${telemetry.seat ? 'Occupied' : 'Ready'}`;
    node1Theme = 'green';
  } else if (isGatewayOnline && node1GatewayStatus === 'ASLEEP_OR_OFFLINE') {
    if (!telemetry.seat) {
      node1Status = 'SLEEP';
      node1BadgeText = 'SLEEP';
      node1Subtext = 'Vacant >60s';
      node1Desc = 'Low-power deep sleep (E3)';
      node1Theme = 'amber';
    } else {
      node1Status = 'OFFLINE';
      node1BadgeText = 'OFFLINE';
      node1Subtext = 'Seated / Lost';
      node1Desc = 'Connection dropped';
      node1Theme = 'red';
    }
  } else if (!isGatewayOnline) {
    if (node1DirectAgeSec !== null && node1DirectAgeSec <= 20) {
      node1Status = 'ONLINE';
      node1BadgeText = 'ONLINE';
      node1Subtext = 'Direct Wi-Fi';
      node1Desc = 'FSR402 Mat active';
      node1Theme = 'green';
    } else {
      node1Status = 'UNKNOWN';
      node1BadgeText = 'OFFLINE';
      node1Subtext = 'GW Offline';
      node1Desc = 'Status unverified';
      node1Theme = 'gray';
    }
  }

  // Node 2 (Desk) connectivity
  const node2DirectAgeSec = lastNode2Rx ? Math.max(0, (currentTime - lastNode2Rx) / 1000) : null;
  const node2GatewayStatus = telemetry.node_status?.node2;
  let node2Status = 'STANDBY';
  let node2BadgeText = 'STANDBY';
  let node2Subtext = 'Awaiting signal';
  let node2Desc = 'BH1750 & KY-037';
  let node2Theme = 'gray';

  if ((node2DirectAgeSec !== null && node2DirectAgeSec <= 4.0) || (isGatewayOnline && node2GatewayStatus === 'ONLINE')) {
    node2Status = 'ONLINE';
    node2BadgeText = 'ONLINE';
    node2Subtext = 'Streaming';
    node2Desc = 'BH1750 Lux & KY-037 Active';
    node2Theme = 'green';
  } else if (isGatewayOnline && node2GatewayStatus === 'STALE') {
    node2Status = 'STALE';
    node2BadgeText = 'STALE';
    node2Subtext = '>5s no packet';
    node2Desc = 'Reconnecting...';
    node2Theme = 'red';
  } else if (!isGatewayOnline) {
    if (node2DirectAgeSec !== null && node2DirectAgeSec <= 4.0) {
      node2Status = 'ONLINE';
      node2BadgeText = 'ONLINE';
      node2Subtext = 'Direct Wi-Fi';
      node2Desc = 'Desk stream active';
      node2Theme = 'green';
    } else {
      node2Status = 'UNKNOWN';
      node2BadgeText = 'OFFLINE';
      node2Subtext = 'GW Offline';
      node2Desc = 'Status unverified';
      node2Theme = 'gray';
    }
  }

  return (
    <div className="min-h-screen bg-[#F4F7F9] p-4 sm:p-6 md:p-8 font-sans text-slate-800 flex flex-col gap-4 max-w-7xl mx-auto">
      
      {/* Header Card */}
      <header className="bg-white rounded-2xl p-5 sm:p-6 shadow-sm border border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3 sm:gap-4 flex-wrap">
          <h1 className="text-2xl sm:text-3xl md:text-4xl font-bold tracking-tight">SmartWorkstation Dashboard</h1>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center gap-1.5 bg-slate-100 text-slate-600 px-3 py-1 rounded-full text-xs font-semibold uppercase tracking-wider flex-shrink-0 whitespace-nowrap">
              <Cpu size={14} className="text-indigo-500" /> RFC Model
            </span>
            <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold flex-shrink-0 whitespace-nowrap ${
              connectionStatus.includes('Live') 
                ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' 
                : connectionStatus.includes('Sync')
                ? 'bg-blue-50 text-blue-700 border border-blue-200'
                : 'bg-amber-50 text-amber-700 border border-amber-200'
            }`}>
              <span className={`w-2 h-2 rounded-full ${
                connectionStatus.includes('Live') 
                  ? 'bg-emerald-500 animate-pulse' 
                  : connectionStatus.includes('Sync')
                  ? 'bg-blue-500'
                  : 'bg-amber-500'
              }`}></span>
              {connectionStatus}
            </span>
          </div>
        </div>

        {/* Live Model Performance Pill */}
        <div className="flex items-center gap-2 text-xs text-slate-500 bg-slate-50 px-3 py-2 rounded-xl border border-slate-100 flex-shrink-0 whitespace-nowrap">
          <Activity size={15} className="text-slate-400" />
          <span>Inference: <strong className="text-slate-700 font-semibold">{Number(telemetry.ai_state?.latency_ms || 0.45).toFixed(2)} ms</strong></span>
          <span className="text-slate-300">|</span>
          <span>Conf: <strong className="text-slate-700 font-semibold">{Math.round((telemetry.ai_state?.confidence || 0.95) * 100)}%</strong></span>
        </div>
      </header>

      {/* Node & Gateway Health / Connectivity Bar */}
      <section className="bg-white rounded-2xl p-4 sm:p-5 shadow-sm border border-slate-100 flex flex-col gap-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Radio size={16} className="text-indigo-600 animate-pulse flex-shrink-0" />
            <h3 className="font-bold text-xs sm:text-sm uppercase tracking-wider text-slate-700">
              Hardware Nodes & Gateway Connectivity
            </h3>
          </div>
          <span className="text-[11px] font-medium text-slate-400 whitespace-nowrap">
            Auto-refreshing (1 Hz)
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Card 1: Gateway Controller */}
          <div className={`p-3 sm:p-3.5 rounded-xl border flex items-center justify-between gap-3 min-w-0 transition-colors ${
            isGatewayOnline ? 'bg-emerald-50/40 border-emerald-200/80' : gatewayStatus === 'LAGGING' ? 'bg-amber-50/40 border-amber-200/80' : 'bg-rose-50/50 border-rose-200'
          }`}>
            <div className="flex items-center gap-2.5 sm:gap-3 min-w-0 flex-1">
              <div className={`p-2 rounded-lg border flex-shrink-0 ${
                isGatewayOnline ? 'bg-emerald-100/70 text-emerald-700 border-emerald-300' : 'bg-rose-100 text-rose-700 border-rose-300'
              }`}>
                <Server size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="font-bold text-sm text-slate-800 truncate">Gateway</span>
                  <span className="text-[10px] font-medium text-slate-400 whitespace-nowrap">(RPi 4)</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5 truncate">
                  {isGatewayOnline ? 'HC-SR04 & DS18B20 Active' : 'Daemon Unreachable'}
                </p>
              </div>
            </div>
            <div className="text-right flex flex-col items-end flex-shrink-0">
              <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] sm:text-[11px] font-bold whitespace-nowrap uppercase tracking-wider ${
                isGatewayOnline ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isGatewayOnline ? 'bg-emerald-500 animate-ping' : 'bg-rose-500'}`}></span>
                {gatewayBadgeText}
              </span>
              <span className="text-[10px] text-slate-400 mt-0.5 whitespace-nowrap">
                {gatewaySubtext}
              </span>
            </div>
          </div>

          {/* Card 2: Sensor Node 1 (Chair) */}
          <div className={`p-3 sm:p-3.5 rounded-xl border flex items-center justify-between gap-3 min-w-0 transition-colors ${
            node1Theme === 'green' ? 'bg-emerald-50/40 border-emerald-200/80' : node1Theme === 'amber' ? 'bg-amber-50/40 border-amber-200/80' : 'bg-slate-50 border-slate-200'
          }`}>
            <div className="flex items-center gap-2.5 sm:gap-3 min-w-0 flex-1">
              <div className={`p-2 rounded-lg border flex-shrink-0 ${
                node1Theme === 'green' ? 'bg-emerald-100/70 text-emerald-700 border-emerald-300' : node1Theme === 'amber' ? 'bg-amber-100 text-amber-700 border-amber-300' : 'bg-slate-200/60 text-slate-600 border-slate-300'
              }`}>
                <Armchair size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="font-bold text-sm text-slate-800 truncate">Node 1</span>
                  <span className="text-[10px] font-medium text-slate-400 whitespace-nowrap">(Chair)</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5 truncate">{node1Desc}</p>
              </div>
            </div>
            <div className="text-right flex flex-col items-end flex-shrink-0">
              <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] sm:text-[11px] font-bold whitespace-nowrap uppercase tracking-wider ${
                node1Theme === 'green' ? 'bg-emerald-100 text-emerald-800' : node1Theme === 'amber' ? 'bg-amber-100 text-amber-800' : 'bg-slate-200 text-slate-700'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${node1Theme === 'green' ? 'bg-emerald-500 animate-ping' : node1Theme === 'amber' ? 'bg-amber-500' : 'bg-slate-400'}`}></span>
                {node1BadgeText}
              </span>
              <span className="text-[10px] text-slate-400 mt-0.5 whitespace-nowrap">
                {node1Subtext}
              </span>
            </div>
          </div>

          {/* Card 3: Sensor Node 2 (Desk) */}
          <div className={`p-3 sm:p-3.5 rounded-xl border flex items-center justify-between gap-3 min-w-0 transition-colors ${
            node2Theme === 'green' ? 'bg-emerald-50/40 border-emerald-200/80' : node2Theme === 'red' ? 'bg-rose-50/50 border-rose-200' : 'bg-slate-50 border-slate-200'
          }`}>
            <div className="flex items-center gap-2.5 sm:gap-3 min-w-0 flex-1">
              <div className={`p-2 rounded-lg border flex-shrink-0 ${
                node2Theme === 'green' ? 'bg-emerald-100/70 text-emerald-700 border-emerald-300' : 'bg-rose-100 text-rose-700 border-rose-300'
              }`}>
                <Sun size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="font-bold text-sm text-slate-800 truncate">Node 2</span>
                  <span className="text-[10px] font-medium text-slate-400 whitespace-nowrap">(Desk)</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5 truncate">{node2Desc}</p>
              </div>
            </div>
            <div className="text-right flex flex-col items-end flex-shrink-0">
              <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] sm:text-[11px] font-bold whitespace-nowrap uppercase tracking-wider ${
                node2Theme === 'green' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${node2Theme === 'green' ? 'bg-emerald-500 animate-ping' : 'bg-rose-500'}`}></span>
                {node2BadgeText}
              </span>
              <span className="text-[10px] text-slate-400 mt-0.5 whitespace-nowrap">
                {node2Subtext}
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* Mandatory Study Break Countdown Banner */}
      {telemetry.break_active && (
        <div className="mb-6 bg-gradient-to-r from-amber-500 via-rose-500 to-red-500 text-white p-4 sm:p-5 rounded-2xl shadow-md flex items-center justify-between flex-wrap gap-3 animate-pulse border border-rose-300">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-white/20 rounded-xl backdrop-blur-sm">
              <Lock className="w-6 h-6 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-bold text-base sm:text-lg">Mandatory Study Break (Curtain Locked)</h3>
                <span className="bg-white/20 text-white text-[10px] font-extrabold uppercase px-2 py-0.5 rounded-full">Enforced</span>
              </div>
              <p className="text-xs sm:text-sm text-white/90">50-minute study interval reached. Stand up, stretch, and rest your eyes.</p>
            </div>
          </div>
          <div className="text-right ml-auto bg-black/15 px-4 py-2 rounded-xl backdrop-blur-sm">
            <span className="block text-[10px] uppercase tracking-wider text-white/80 font-bold">Unlocks In</span>
            <span className="text-2xl sm:text-3xl font-extrabold font-mono tracking-tight">
              {Math.floor(telemetry.break_remaining_s / 60)}:{String(telemetry.break_remaining_s % 60).padStart(2, '0')}
            </span>
          </div>
        </div>
      )}

      {/* AI Ergonomic Closed-Loop Status Banner */}
      <section className={`rounded-2xl p-5 sm:p-6 border transition-all flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-sm ${bannerBg}`}>
        <div className="flex items-start sm:items-center gap-4">
          <div className={`p-2.5 rounded-xl border flex-shrink-0 ${iconBoxStyle}`}>
            {isAlert ? <AlertTriangle className="w-7 h-7" /> : <CheckCircle2 className="w-7 h-7" />}
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-lg sm:text-xl font-bold tracking-tight text-slate-900">
                {currentRfc.name}
              </h2>
              <span className={`text-[11px] font-bold px-2 py-0.5 rounded uppercase tracking-wider ${badgeStyle}`}>
                {telemetry.curtain_state === 'CLOSED' ? 'Curtain Closed (100%)' : telemetry.curtain_state === 'GLARE_SHIELD' ? 'Glare Shield (40%)' : 'Curtain Open (0%)'}
              </span>
            </div>
            <p className="text-xs sm:text-sm text-slate-600 mt-0.5">{adviceText}</p>
          </div>
        </div>

        {/* Actuator closed-loop status strip */}
        <div className="flex items-center gap-2 sm:gap-3 bg-white/70 backdrop-blur-sm p-2.5 rounded-xl border border-black/5 self-stretch sm:self-auto justify-around sm:justify-start text-xs font-medium text-slate-700">
          <div className="text-center px-2">
            <span className="block text-[10px] uppercase font-bold text-slate-400">Curtain</span>
            <span className="font-bold text-slate-800">{telemetry.curtain_state}</span>
          </div>
          <div className="h-6 w-[1px] bg-slate-200"></div>
          <div className="text-center px-2">
            <span className="block text-[10px] uppercase font-bold text-slate-400">Desk Lamp</span>
            <span className="font-bold text-slate-800">{telemetry.led_duty_cycle.toFixed(1)}%</span>
          </div>
        </div>
      </section>

      {/* Grid of Telemetry Cards: 6-Column Responsive Grid */}
      <main className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4">
        
        {/* Card 1: Study Session Time */}
        <div className={`rounded-2xl p-4 sm:p-5 border flex flex-col justify-between transition-colors shadow-sm min-h-[200px] sm:min-h-[220px] ${
          sittingMin > 60 ? 'bg-[#FFF0F2] border-[#FFCBD3]' : 'bg-white border-slate-100'
        }`}>
          <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg">
            <div className={`p-2 rounded-lg border ${
              sittingMin > 60 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
            }`}>
              <Clock size={20} />
            </div>
            Study Time
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {sittingMin.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">min</span>
            </div>
            <span className="text-xs text-slate-500 font-medium mt-1">
              {formattedSessionTime}
            </span>
          </div>

          <div className="rounded-xl p-2.5 bg-[#F4F7F9] flex flex-col justify-center">
            <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase mb-1">
              <span>Goal: &lt;60 min</span>
              <span className={sittingMin > 60 ? 'text-rose-600' : 'text-slate-500'}>
                {sittingMin > 60 ? 'Break Due' : 'Active'}
              </span>
            </div>
            <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all duration-500 ${
                  sittingMin > 60 ? 'bg-rose-500' : 'bg-emerald-500'
                }`}
                style={{ width: `${Math.min(100, (sittingMin / 60) * 100)}%` }}
              ></div>
            </div>
          </div>
        </div>

        {/* Card 2: Seat Presence */}
        <div className={`rounded-2xl p-4 sm:p-5 border flex flex-col justify-between transition-colors shadow-sm min-h-[200px] sm:min-h-[220px] ${
          !telemetry.seat ? 'bg-[#FFF0F2] border-[#FFCBD3]' : 'bg-white border-slate-100'
        }`}>
          <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
            <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg min-w-0">
              <div className={`p-2 rounded-lg border flex-shrink-0 ${
                !telemetry.seat ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
              }`}>
                <Armchair size={20} />
              </div>
              <span className="truncate">Seat</span>
            </div>
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0 whitespace-nowrap ${
              node1Theme === 'green' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : node1Theme === 'amber' ? 'bg-amber-50 text-amber-700 border border-amber-200' : 'bg-slate-100 text-slate-600'
            }`}>
              Node 1 • {node1Status}
            </span>
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className={`text-3xl sm:text-4xl font-bold tracking-tight ${
              telemetry.seat ? 'text-emerald-600' : 'text-slate-400'
            }`}>
              {telemetry.seat ? 'SEATED' : 'EMPTY'}
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              FSR402 Force Sensing Resistor (Node 1 GPIO 33)
            </span>
          </div>

          <div className="rounded-xl p-2.5 bg-[#F4F7F9] flex items-center justify-between text-[11px] font-medium text-slate-500">
            <span className="text-[10px] font-bold text-slate-400 uppercase">State</span>
            <span className={`font-semibold ${telemetry.seat ? 'text-emerald-600' : 'text-slate-500'}`}>
              {telemetry.seat ? 'Occupied' : 'Vacant (Sleep)'}
            </span>
          </div>
        </div>

        {/* Card 3: Posture Distance */}
        <div className={`rounded-2xl p-4 sm:p-5 border flex flex-col justify-between transition-colors shadow-sm min-h-[200px] sm:min-h-[220px] ${
          telemetry.distance < 30 || telemetry.distance > 70 ? 'bg-[#FFF0F2] border-[#FFCBD3]' : 'bg-white border-slate-100'
        }`}>
          <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
            <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg min-w-0">
              <div className={`p-2 rounded-lg border flex-shrink-0 ${
                telemetry.distance < 30 || telemetry.distance > 70 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
              }`}>
                <User size={20} />
              </div>
              <span className="truncate">Distance</span>
            </div>
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0 whitespace-nowrap ${
              isGatewayOnline ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-rose-50 text-rose-700 border border-rose-200'
            }`}>
              Gateway • {gatewayStatus}
            </span>
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {telemetry.distance.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">cm</span>
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              HC-SR04 Ultrasonic Sensor (RPi GPIO 23/24)
            </span>
          </div>

          <div className="rounded-xl p-2.5 bg-[#F4F7F9] flex flex-col justify-center">
            <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase mb-1">
              <span>Ergonomic Zone</span>
              <span className={telemetry.distance < 30 ? 'text-rose-600' : 'text-slate-500'}>
                {telemetry.distance < 30 ? 'Too Close' : 'Optimal'}
              </span>
            </div>
            <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all duration-500 ${
                  telemetry.distance < 30 ? 'bg-rose-500' : 'bg-emerald-500'
                }`}
                style={{ width: `${Math.min(100, Math.max(10, (telemetry.distance / 80) * 100))}%` }}
              ></div>
            </div>
          </div>
        </div>

        {/* Card 4: Ambient Light */}
        <div className={`rounded-2xl p-4 sm:p-5 border flex flex-col justify-between transition-colors shadow-sm min-h-[200px] sm:min-h-[220px] ${
          telemetry.light < 300 || telemetry.light > 1000 ? 'bg-[#FFF0F2] border-[#FFCBD3]' : 'bg-white border-slate-100'
        }`}>
          <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
            <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg min-w-0">
              <div className={`p-2 rounded-lg border flex-shrink-0 ${
                telemetry.light < 300 || telemetry.light > 1000 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
              }`}>
                <Sun size={20} />
              </div>
              <span className="truncate">Light</span>
            </div>
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0 whitespace-nowrap ${
              node2Theme === 'green' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-rose-50 text-rose-700 border border-rose-200'
            }`}>
              Node 2 • {node2Status}
            </span>
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {telemetry.light.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">Lux</span>
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              BH1750 Ambient Light Sensor (Node 2 I2C GPIO 21/22)
            </span>
          </div>

          <div className="rounded-xl p-2.5 bg-[#F4F7F9] flex flex-col justify-center">
            <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase mb-1">
              <span>Standard: 300–800</span>
              <span className={telemetry.light < 300 ? 'text-amber-600' : telemetry.light > 1000 ? 'text-rose-600' : 'text-slate-500'}>
                {telemetry.light < 300 ? 'Dim' : telemetry.light > 1000 ? 'Glare' : 'Ideal'}
              </span>
            </div>
            <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all duration-500 ${
                  telemetry.light < 300 ? 'bg-amber-500' : telemetry.light > 1000 ? 'bg-rose-500' : 'bg-emerald-500'
                }`}
                style={{ width: `${Math.min(100, Math.max(5, (telemetry.light / 1200) * 100))}%` }}
              ></div>
            </div>
          </div>
        </div>

        {/* Card 5: Temperature */}
        <div className={`rounded-2xl p-4 sm:p-5 border flex flex-col justify-between transition-colors shadow-sm min-h-[200px] sm:min-h-[220px] ${
          telemetry.temp > 28.0 || telemetry.temp < 20.0 ? 'bg-[#FFF0F2] border-[#FFCBD3]' : 'bg-white border-slate-100'
        }`}>
          <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
            <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg min-w-0">
              <div className={`p-2 rounded-lg border flex-shrink-0 ${
                telemetry.temp > 28.0 || telemetry.temp < 20.0 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
              }`}>
                <Thermometer size={20} />
              </div>
              <span className="truncate">Temp</span>
            </div>
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0 whitespace-nowrap ${
              isGatewayOnline ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-rose-50 text-rose-700 border border-rose-200'
            }`}>
              Gateway • {gatewayStatus}
            </span>
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {telemetry.temp.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">°C</span>
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              KY-001 DS18B20 Temp Sensor (RPi GPIO 4)
            </span>
          </div>

          <div className="rounded-xl p-2.5 bg-[#F4F7F9] flex flex-col justify-center">
            <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase mb-1">
              <span>Comfort: 22–26°C</span>
              <span className={telemetry.temp > 28 ? 'text-rose-600' : 'text-slate-500'}>
                {telemetry.temp > 28 ? 'Too Warm' : 'Comfort'}
              </span>
            </div>
            <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all duration-500 ${
                  telemetry.temp > 28 ? 'bg-rose-500' : 'bg-emerald-500'
                }`}
                style={{ width: `${Math.min(100, Math.max(10, ((telemetry.temp - 15) / 25) * 100))}%` }}
              ></div>
            </div>
          </div>
        </div>

        {/* Card 6: Acoustic Noise */}
        <div className={`rounded-2xl p-4 sm:p-5 border flex flex-col justify-between transition-colors shadow-sm min-h-[200px] sm:min-h-[220px] ${
          telemetry.sound > 65.0 ? 'bg-[#FFF0F2] border-[#FFCBD3]' : 'bg-white border-slate-100'
        }`}>
          <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
            <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg min-w-0">
              <div className={`p-2 rounded-lg border flex-shrink-0 ${
                telemetry.sound > 65.0 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
              }`}>
                <Volume2 size={20} />
              </div>
              <span className="truncate">Sound</span>
            </div>
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0 whitespace-nowrap ${
              node2Theme === 'green' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-rose-50 text-rose-700 border border-rose-200'
            }`}>
              Node 2 • {node2Status}
            </span>
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {telemetry.sound.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">dB</span>
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              KY-037 Sound Sensor (Node 2 GPIO 35)
            </span>
          </div>

          <div className="rounded-xl p-2.5 bg-[#F4F7F9] flex flex-col justify-center">
            <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase mb-1">
              <span>Quiet: &lt;50 dB</span>
              <span className={telemetry.sound > 65 ? 'text-rose-600' : 'text-slate-500'}>
                {telemetry.sound > 65 ? 'Noisy' : 'Quiet Focus'}
              </span>
            </div>
            <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
              <div 
                className={`h-full rounded-full transition-all duration-500 ${
                  telemetry.sound > 65 ? 'bg-rose-500' : 'bg-emerald-500'
                }`}
                style={{ width: `${Math.min(100, Math.max(5, ((telemetry.sound - 30) / 55) * 100))}%` }}
              ></div>
            </div>
          </div>
        </div>

      </main>

      {/* Hardware Components & Actuators BOM (Below Sensor Values) */}
      <footer className="mt-2 bg-white rounded-2xl p-5 sm:p-6 shadow-sm border border-slate-100 flex flex-col gap-4">
        <div className="flex items-center justify-between flex-wrap gap-2 pb-3 border-b border-slate-100">
          <div className="flex items-center gap-2.5">
            <div className="p-2 bg-indigo-50 text-indigo-600 rounded-lg border border-indigo-100">
              <CircuitBoard size={20} />
            </div>
            <div>
              <h3 className="font-bold text-base sm:text-lg text-slate-800">Hardware Components & Actuators</h3>
              <p className="text-xs text-slate-500">Physical motion devices, compute nodes, and circuit interfaces beyond environmental sensors</p>
            </div>
          </div>
          <span className="text-xs font-semibold px-2.5 py-1 bg-slate-100 text-slate-600 rounded-full">
            CEDT 2110356 BOM
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          {/* Column 1: Actuators (Requirement M2) */}
          <div className="bg-[#F8FAFC] rounded-xl p-4 border border-slate-100 flex flex-col gap-3 min-w-0">
            <div className="flex items-center gap-2 font-bold text-slate-700 uppercase tracking-wider text-[11px] min-w-0">
              <Zap size={14} className="text-amber-500 flex-shrink-0" />
              <span className="truncate">Closed-Loop Actuators (M2)</span>
            </div>
            <div className="flex flex-col gap-2.5">
              <div className="bg-white p-2.5 rounded-lg border border-slate-200/60 shadow-xs">
                <div className="flex items-center justify-between gap-1.5 flex-wrap font-semibold text-slate-800">
                  <span className="truncate">28BYJ-48 Stepper Motor</span>
                  <span className="text-[10px] bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded font-mono flex-shrink-0 whitespace-nowrap">ULN2003</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5">Smart Blocker Curtain (M2 Physical Motion, M7 bounds)</p>
                <div className="text-[10px] text-slate-400 mt-1 font-mono break-all sm:break-normal">RPi GPIO 5, 6, 13, 19 (IN1–IN4)</div>
              </div>
              <div className="bg-white p-2.5 rounded-lg border border-slate-200/60 shadow-xs">
                <div className="flex items-center justify-between gap-1.5 flex-wrap font-semibold text-slate-800">
                  <span className="truncate">5V COB LED Task Lamp</span>
                  <span className="text-[10px] bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded font-mono flex-shrink-0 whitespace-nowrap">PWM0</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5">Adaptive Illumination (Method 2 via ULN2003 Ch 5)</p>
                <div className="text-[10px] text-slate-400 mt-1 font-mono break-all sm:break-normal">RPi GPIO 18 (IN5) → OUT5 to COB [-]</div>
              </div>
            </div>
          </div>

          {/* Column 2: Compute & Microcontrollers */}
          <div className="bg-[#F8FAFC] rounded-xl p-4 border border-slate-100 flex flex-col gap-3 min-w-0">
            <div className="flex items-center gap-2 font-bold text-slate-700 uppercase tracking-wider text-[11px] min-w-0">
              <Server size={14} className="text-indigo-500 flex-shrink-0" />
              <span className="truncate">Compute & Microcontrollers</span>
            </div>
            <div className="flex flex-col gap-2.5">
              <div className="bg-white p-2.5 rounded-lg border border-slate-200/60 shadow-xs">
                <div className="flex items-center justify-between gap-1.5 flex-wrap font-semibold text-slate-800">
                  <span className="truncate">Raspberry Pi 4 Model B</span>
                  <span className="text-[10px] bg-purple-50 text-purple-700 px-1.5 py-0.5 rounded font-mono flex-shrink-0 whitespace-nowrap">Edge AI</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5">Central Gateway, Random Forest AI (M4), SQLite queue (E4)</p>
                <div className="text-[10px] text-slate-400 mt-1 font-mono break-all sm:break-normal">Linux 1-Wire & Hardware PWM0</div>
              </div>
              <div className="bg-white p-2.5 rounded-lg border border-slate-200/60 shadow-xs">
                <div className="flex items-center justify-between gap-1.5 flex-wrap font-semibold text-slate-800">
                  <span className="truncate">ESP32 DevKit V1 (Nodes 1 & 2)</span>
                  <span className="text-[10px] bg-amber-50 text-amber-700 px-1.5 py-0.5 rounded font-mono flex-shrink-0 whitespace-nowrap">FreeRTOS</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5">Chair Node 1 (E2/E3 Deep Sleep) & Desk Node 2 (E1 Multitasking)</p>
                <div className="text-[10px] text-slate-400 mt-1 font-mono break-all sm:break-normal">Wi-Fi MQTT Telemetry w/ Sequence Retry (E4)</div>
              </div>
            </div>
          </div>

          {/* Column 3: Circuit & Power Infrastructure */}
          <div className="bg-[#F8FAFC] rounded-xl p-4 border border-slate-100 flex flex-col gap-3 min-w-0">
            <div className="flex items-center gap-2 font-bold text-slate-700 uppercase tracking-wider text-[11px] min-w-0">
              <Layers size={14} className="text-emerald-500 flex-shrink-0" />
              <span className="truncate">Circuit & Power Support</span>
            </div>
            <div className="flex flex-col gap-2.5">
              <div className="bg-white p-2.5 rounded-lg border border-slate-200/60 shadow-xs">
                <div className="flex items-center justify-between gap-1.5 flex-wrap font-semibold text-slate-800">
                  <span className="truncate">Workstation 5V Power Rail</span>
                  <span className="text-[10px] bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded font-mono flex-shrink-0 whitespace-nowrap">5V / GND</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5">Common Ground power bus for RPi, ESP32, Stepper & COB LED</p>
                <div className="text-[10px] text-slate-400 mt-1 font-mono break-all sm:break-normal">Regulated 5V DC step-down power supply</div>
              </div>
              <div className="bg-white p-2.5 rounded-lg border border-slate-200/60 shadow-xs">
                <div className="flex items-center justify-between gap-1.5 flex-wrap font-semibold text-slate-800">
                  <span className="truncate">Protection & Resistor Dividers</span>
                  <span className="text-[10px] bg-rose-50 text-rose-700 px-1.5 py-0.5 rounded font-mono flex-shrink-0 whitespace-nowrap">Safety</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-0.5">1kΩ/2kΩ HC-SR04 Echo divider (5V→3.3V) & 10kΩ FSR pull-down</p>
                <div className="text-[10px] text-slate-400 mt-1 font-mono break-all sm:break-normal">Protects Pi GPIO & stabilizes seat wake interrupt</div>
              </div>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
