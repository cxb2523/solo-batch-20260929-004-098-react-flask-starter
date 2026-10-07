import React, { useEffect, useState } from 'react';
import {
  Box,
  Chip,
  Link,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@material-ui/core';
import { makeStyles } from '@material-ui/core/styles';

const SOURCE_STYLES = {
  default: { color: '#9e9e9e', label: 'default' },
  'environment variable': { color: '#35baf6', label: 'environment variable' },
  'compose environment': { color: '#ffb74d', label: 'compose environment' },
  'compose env_file': { color: '#ba68c8', label: 'compose env_file' },
};

const useStyles = makeStyles(() => ({
  root: {
    padding: '32px',
    maxWidth: '1100px',
    margin: '0 auto',
  },
  banner: {
    padding: '12px 16px',
    borderRadius: '6px',
    marginBottom: '16px',
    fontWeight: 600,
  },
  drift: {
    background: '#4a1010',
    border: '1px solid #f44336',
    color: '#ff8a80',
  },
  pending: {
    background: '#4a3a10',
    border: '1px solid #ffb74d',
    color: '#ffcc80',
  },
  overriddenRow: {
    borderLeft: '4px solid #ffb74d',
  },
  driftRow: {
    borderLeft: '4px solid #f44336',
    background: 'rgba(244, 67, 54, 0.12)',
  },
  mono: {
    fontFamily: 'monospace',
  },
  notes: {
    marginTop: '24px',
    '& li': {
      marginBottom: '6px',
    },
  },
}));

function formatValue(value) {
  if (value === null || value === undefined) return 'null';
  return String(value);
}

export default function SystemPanel() {
  const classes = useStyles();
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/system/audit')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setReport)
      .catch((err) => setError(err.message));
  }, []);

  if (error) {
    return (
      <Box className={classes.root}>
        <Paper className={`${classes.banner} ${classes.drift}`}>
          Failed to load /api/system/audit: {error}
        </Paper>
      </Box>
    );
  }
  if (!report) {
    return <Box className={classes.root}>Loading audit report…</Box>;
  }

  const drifted = report.drift && report.drift.length > 0;

  return (
    <Box className={classes.root}>
      <Typography variant="h4" gutterBottom>
        System audit <Typography variant="caption">(read-only)</Typography>
      </Typography>
      <Typography variant="body2" gutterBottom>
        role: <code>{report.role}</code>
        {' · '}alembic head: <code>{report.alembic.head || 'unknown'}</code>
        {' · '}current: <code>{report.alembic.current || 'unavailable'}</code>
        {' · '}migrations:{' '}
        <Chip
          size="small"
          label={report.alembic.ready ? 'ready' : 'pending'}
          color={report.alembic.ready ? 'primary' : 'secondary'}
        />
      </Typography>

      {drifted && (
        <Paper className={`${classes.banner} ${classes.drift}`}>
          Config drift detected between the audit contract and src.settings on:{' '}
          {report.drift.join(', ')}. The CLI exits non-zero on this state; do
          not trust either side until the two parse paths agree again.
        </Paper>
      )}
      {!report.alembic.ready && (
        <Paper className={`${classes.banner} ${classes.pending}`}>
          Database migrations are pending — the worker runs them once on
          start; data endpoints return 503 until the head revision is applied.
        </Paper>
      )}

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Key</TableCell>
              <TableCell>Effective value</TableCell>
              <TableCell>Source layer</TableCell>
              <TableCell>Status</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {report.keys.map((key) => {
              const source = SOURCE_STYLES[key.source] || SOURCE_STYLES.default;
              const rowClass =
                key.status === 'drift'
                  ? classes.driftRow
                  : key.overridden
                  ? classes.overriddenRow
                  : '';
              return (
                <TableRow key={key.name} className={rowClass}>
                  <TableCell className={classes.mono}>{key.name}</TableCell>
                  <TableCell className={classes.mono}>
                    {formatValue(key.value)}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={source.label}
                      style={{ color: source.color, borderColor: source.color }}
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell>
                    {key.status === 'drift' ? (
                      <span style={{ color: '#ff8a80' }}>
                        DRIFT (settings={formatValue(key.settings_value)})
                      </span>
                    ) : (
                      'ok'
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>

      <Box className={classes.notes}>
        <Typography variant="h6" gutterBottom>
          Why local and container values can differ
        </Typography>
        <ul>
          {report.notes.map((note, i) => (
            <li key={i}>
              <Typography variant="body2">{note}</Typography>
            </li>
          ))}
        </ul>
        <Typography variant="body2">
          Compare with the CLI: <code>python -m backend.src.entry audit --json</code>{' '}
          — every key row must match this table. Back to{' '}
          <Link href="/">home</Link>.
        </Typography>
      </Box>
    </Box>
  );
}
