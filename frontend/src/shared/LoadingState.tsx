type LoadingStateProps = {
  label: string;
  variant?: 'card' | 'table' | 'auth';
};

/**
 * Non-blocking loading surface. The accessible label remains available to
 * assistive technology while the visual treatment reserves the page geometry
 * without flashing a large textual loading card during navigation.
 */
export function LoadingState({ label, variant = 'card' }: LoadingStateProps) {
  return <div className={`loading-state loading-state-${variant}`} role="status" aria-live="polite" aria-label={label}>
    <span className="loading-state-message">{label}</span>
    <span className="loading-state-line loading-state-line-wide" aria-hidden="true"/>
    <span className="loading-state-line" aria-hidden="true"/>
    <span className="loading-state-line loading-state-line-short" aria-hidden="true"/>
  </div>;
}
