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
  Activity
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
};

export default function App() {
  const [connectionStatus, setConnectionStatus] = useState('Connecting...');
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
    ai_state: {
      class_id: 0,
      class_name: 'OPTIMAL_STUDY',
      confidence: 0.96,
      latency_ms: 0.45
    },
    timestamp: Date.now() / 1000,
    _source: 'init'
  });

  useEffect(() => {
    const unsubscribe = subscribeToWorkstationTelemetry(
      (data) => {
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
          ai_state: data.ai_state || prev.ai_state,
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

  return (
    <div className="min-h-screen bg-[#F4F7F9] p-4 sm:p-6 md:p-8 font-sans text-slate-800 flex flex-col gap-4 max-w-7xl mx-auto">
      
      {/* Header Card */}
      <header className="bg-white rounded-2xl p-5 sm:p-6 shadow-sm border border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3 sm:gap-4 flex-wrap">
          <h1 className="text-2xl sm:text-3xl md:text-4xl font-bold tracking-tight">Smart Workstation</h1>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 bg-slate-100 text-slate-600 px-3 py-1 rounded-full text-xs font-semibold uppercase tracking-wider">
              <Cpu size={14} className="text-indigo-500" /> RFC Model
            </span>
            <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
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
        <div className="flex items-center gap-2 text-xs text-slate-500 bg-slate-50 px-3 py-2 rounded-xl border border-slate-100">
          <Activity size={15} className="text-slate-400" />
          <span>Inference: <strong className="text-slate-700 font-semibold">{Number(telemetry.ai_state?.latency_ms || 0.45).toFixed(2)} ms</strong></span>
          <span className="text-slate-300">|</span>
          <span>Conf: <strong className="text-slate-700 font-semibold">{Math.round((telemetry.ai_state?.confidence || 0.95) * 100)}%</strong></span>
        </div>
      </header>

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
          <div className="h-6 w-[1px] bg-slate-200"></div>
          <div className="text-center px-2">
            <span className="block text-[10px] uppercase font-bold text-slate-400">Nudge Servo</span>
            <span className="font-bold text-slate-800">{telemetry.servo_angle.toFixed(1)}°</span>
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
          <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg">
            <div className={`p-2 rounded-lg border ${
              !telemetry.seat ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
            }`}>
              <Armchair size={20} />
            </div>
            Seat
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className={`text-3xl sm:text-4xl font-bold tracking-tight ${
              telemetry.seat ? 'text-emerald-600' : 'text-slate-400'
            }`}>
              {telemetry.seat ? 'SEATED' : 'EMPTY'}
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              FSR Mat Pin 33
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
          <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg">
            <div className={`p-2 rounded-lg border ${
              telemetry.distance < 30 || telemetry.distance > 70 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
            }`}>
              <User size={20} />
            </div>
            Distance
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {telemetry.distance.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">cm</span>
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              HC-SR04 Ultrasonic
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
          <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg">
            <div className={`p-2 rounded-lg border ${
              telemetry.light < 300 || telemetry.light > 1000 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
            }`}>
              <Sun size={20} />
            </div>
            Light
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {telemetry.light.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">Lux</span>
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              BH1750 I2C
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
          <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg">
            <div className={`p-2 rounded-lg border ${
              telemetry.temp > 28.0 || telemetry.temp < 20.0 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
            }`}>
              <Thermometer size={20} />
            </div>
            Temp
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {telemetry.temp.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">°C</span>
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              Node 2 GPIO 4
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
          <div className="flex items-center gap-2.5 font-bold text-base sm:text-lg">
            <div className={`p-2 rounded-lg border ${
              telemetry.sound > 65.0 ? 'bg-[#FFF0F2] text-rose-500 border-[#FFCBD3]' : 'bg-[#E5FDEB] text-[#22C55E] border-green-200'
            }`}>
              <Volume2 size={20} />
            </div>
            Sound
          </div>

          <div className="my-auto flex flex-col items-center justify-center text-center py-2">
            <div className="text-3xl sm:text-4xl font-bold tracking-tight text-slate-900">
              {telemetry.sound.toFixed(1)}
              <span className="text-xs font-semibold text-slate-400 ml-1">dB</span>
            </div>
            <span className="text-xs text-slate-400 font-medium mt-1">
              Node 2 KY-037 Mic
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
    </div>
  );
}
