import { Activity, AlertTriangle, CheckCircle2, Clock3, ShieldCheck } from 'lucide-react';
import type { OperationsHealth } from '@/lib/admin-api';

type Props = { health: OperationsHealth | null };

const ko = {
  title: '\uC6B4\uC601 \uC548\uC815\uC131',
  subtitle: '\uC790\uB3D9\uD654, \uB370\uC774\uD130 \uC2E0\uC120\uB3C4, \uBAA8\uB378 \uC2B9\uC778 \uC0C1\uD0DC',
  empty: '\uC6B4\uC601 \uC0C1\uD0DC\uB97C \uBD88\uB7EC\uC624\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4.',
  approved: '\uD640\uB4DC\uC544\uC6C3 \uC2B9\uC778',
  blocked: '\uCD94\uCC9C \uC790\uB3D9 \uC911\uC9C0',
  noRun: '\uAC80\uC99D \uC2E4\uD589 \uB300\uAE30',
  latestRun: '\uCD5C\uADFC \uBC31\uD14C\uC2A4\uD2B8',
  automation: '\uC790\uB3D9\uD654',
  data: '\uB370\uC774\uD130',
  days: '\uC77C \uACBD\uACFC',
  recovery: '\uAC00\uACA9 \uC218\uC9D1 \uD68C\uBCF5',
  healthy: '\uC815\uC0C1',
  recoverable: '\uC790\uB3D9 \uD68C\uBCF5 \uB300\uC0C1',
  eligible: '\uC989\uC2DC \uC7AC\uC2DC\uB3C4',
  action: '\uD655\uC778 \uD544\uC694',
};

function tone(status: string) {
  return status === 'ok' || status === 'approved' ? 'good'
    : status === 'critical' || status === 'degraded' || status === 'stale' || status === 'action_required' ? 'bad' : 'warn';
}

function latestLabel(value: string | null, ageDays: number | null) {
  if (!value) return ko.noRun;
  return `${new Date(value).toLocaleString('ko-KR')} / ${ageDays?.toFixed(1) ?? '-'}${ko.days}`;
}

export function OperationsHealthPanel({ health }: Props) {
  if (!health) return <section className="ops-health empty"><AlertTriangle size={20} /><span>{ko.empty}</span></section>;
  const model = health.model_health;
  const recovery = health.price_recovery;
  return <section className={`ops-health ${tone(health.status)}`} aria-label={ko.title}>
    <header>
      <div><span>OPERATIONS HEALTH</span><h2>{ko.title}</h2><p>{ko.subtitle}</p></div>
      <span className={`admin-badge ${tone(health.status)}`}>{health.status}</span>
    </header>
    <article className="ops-recovery">
      <div className="ops-recovery-heading">
        <div><small>{ko.recovery}</small><strong>{recovery.progress_percent.toFixed(2)}%</strong></div>
        <span className={'admin-badge ' + tone(recovery.status)}>{recovery.status.replaceAll('_', ' ')}</span>
      </div>
      <div className="ops-recovery-track" role="progressbar" aria-valuenow={recovery.progress_percent} aria-valuemin={0} aria-valuemax={100}>
        <span style={{ width: String(Math.min(100, recovery.progress_percent)) + '%' }} />
      </div>
      <div className="ops-recovery-counts">
        <span>{ko.healthy} <b>{recovery.healthy}</b></span>
        <span>{ko.recoverable} <b>{recovery.recoverable_failures}</b></span>
        <span>{ko.eligible} <b>{recovery.retry_eligible}</b></span>
        <span>{ko.action} <b>{recovery.action_required}</b></span>
      </div>
    </article>
    <div className="ops-health-grid">
      <article>
        <ShieldCheck size={19} />
        <div><small>IMPACT V4 5D</small><strong>{model.approved ? ko.approved : ko.blocked}</strong><span>{ko.latestRun} #{model.latest_run_id ?? '-'}</span></div>
        <span className={`admin-badge ${tone(model.status)}`}>{model.status}</span>
      </article>
      {health.automation.map((item) => <article key={item.job_name}>
        {item.status === 'ok' ? <CheckCircle2 size={19} /> : <Clock3 size={19} />}
        <div><small>{ko.automation}</small><strong>{item.job_name.replaceAll('_', ' ')}</strong><span>{latestLabel(item.latest_success_at, item.age_days)}</span></div>
        <span className={`admin-badge ${tone(item.status)}`}>{item.status}</span>
      </article>)}
      {health.data_freshness.map((item) => <article key={item.source}>
        <Activity size={19} />
        <div><small>{ko.data}</small><strong>{item.source.replaceAll('_', ' ')}</strong><span>{latestLabel(item.latest_at, item.age_days)}</span></div>
        <span className={`admin-badge ${tone(item.status)}`}>{item.status}</span>
      </article>)}
    </div>
  </section>;
}
