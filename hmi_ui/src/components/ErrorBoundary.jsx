import React from 'react';

/**
 * Keeps one failing page or chart from blanking the whole HMI. Shows what went
 * wrong (useful on a panel with no developer tools) and retries when the
 * page, incident or data changes (resetKey).
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, key: props.resetKey };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  static getDerivedStateFromProps(props, state) {
    if (props.resetKey !== state.key) return { error: null, key: props.resetKey };
    return null;
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error(`OpsLens: ${this.props.label || 'a panel'} failed to draw`, error, info?.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="hm-banner bad" role="alert" style={{ margin: 8 }}>
        <span>
          {this.props.label || 'This panel'} could not be drawn on this screen: {String(error?.message || error)}.{' '}
          <button type="button" className="hm-mini" onClick={() => this.setState({ error: null })}>Try again</button>
        </span>
      </div>
    );
  }
}
