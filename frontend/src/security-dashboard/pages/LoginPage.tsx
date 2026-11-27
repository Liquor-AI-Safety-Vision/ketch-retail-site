import DarkModeOutlinedIcon from '@mui/icons-material/DarkModeOutlined';
import LightModeOutlinedIcon from '@mui/icons-material/LightModeOutlined';
import VisibilityOffOutlinedIcon from '@mui/icons-material/VisibilityOffOutlined';
import VisibilityOutlinedIcon from '@mui/icons-material/VisibilityOutlined';
import { Alert, Box, Button, IconButton, InputAdornment, Stack, TextField, Typography } from '@mui/material';
import { useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { BrandMark } from '../components/BrandMark';
import { getErrorMessage } from '../lib/api';
import { useColorMode } from '../../theme/ColorModeProvider';

const allowedPostLoginPaths = new Set(['/dashboard', '/alerts', '/cameras']);

export function LoginPage() {
  const { user, login, isLoading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { mode, toggleMode } = useColorMode();
  const from = (location.state as { from?: { pathname?: string; search?: string } } | null)?.from;
  const expired = Boolean((location.state as { expired?: boolean } | null)?.expired);
  const destination = from?.pathname && allowedPostLoginPaths.has(from.pathname)
    ? `${from.pathname}${typeof from.search === 'string' && from.search.startsWith('?') ? from.search : ''}`
    : '/dashboard';

  if (!isLoading && user) return <Navigate to={destination} replace />;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate(destination, { replace: true });
    } catch (cause) {
      setError(getErrorMessage(cause));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box sx={{ minHeight: '100dvh', display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 7fr) minmax(420px, 5fr)' }, bgcolor: 'background.default', overflow: 'hidden' }}>
      {/* Below 1200 px the picture shrinks to a brand band, so the form and its
          Sign in button are on screen without scrolling (phones, iPad landscape). */}
      <Box sx={{ minHeight: { lg: '100dvh' }, position: 'relative', overflow: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: 2, px: { xs: 2.5, sm: 4, lg: 7 }, py: { xs: 2, sm: 2.5, lg: 7 } }}>
        {/* A frame from the product's own demo camera (also shown on the landing
            page), not stock photography. */}
        <Box component="img" src="/live-cameras/record-whole-store-153158.jpg" alt="" sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', filter: 'grayscale(.35) contrast(1.05) brightness(.62)' }} />
        <Box sx={{ position: 'absolute', inset: 0, background: 'linear-gradient(135deg, rgba(9,11,12,.3), rgba(9,11,12,.88) 78%), radial-gradient(circle at 20% 15%, rgba(18,140,138,.28), transparent 42%)' }} />
        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ position: 'relative' }}>
          <BrandMark size={36} />
          <Box>
            <Typography color="#F4EFE6" fontWeight={600} lineHeight={1}>
              Ketch Enterprise <Box component="span" sx={{ color: '#A9D8D3' }}>AI</Box>
            </Typography>
            <Typography sx={{ color: '#B1A996', fontSize: 12, letterSpacing: '0.08em', mt: 0.5 }}>RETAIL LOSS PREVENTION</Typography>
          </Box>
        </Stack>
        <Box sx={{ position: 'relative', maxWidth: 900, display: { xs: 'none', lg: 'block' } }}>
          <Typography component="p" sx={{ color: '#F4EFE6', fontSize: 'clamp(3rem, 6vw, 7rem)', lineHeight: 0.92, letterSpacing: '-0.04em', fontWeight: 400, maxWidth: 1040 }}>
            Stop loss before it leaves the store
          </Typography>
          <Typography sx={{ color: '#C4BEB3', mt: 3, maxWidth: 500, lineHeight: 1.75 }}>
            Review camera-backed alerts, verify what happened, and act before a suspicious event becomes a loss.
          </Typography>
        </Box>
      </Box>

      <Box sx={{ display: 'grid', placeItems: { xs: 'start center', sm: 'center' }, px: { xs: 2.5, sm: 6, lg: 8 }, py: { xs: 3, sm: 5, lg: 8 }, position: 'relative' }}>
        <IconButton
          onClick={toggleMode}
          aria-label={`Switch to ${mode === 'light' ? 'dark' : 'light'} mode`}
          title={`Switch to ${mode === 'light' ? 'dark' : 'light'} mode`}
          sx={{ position: 'absolute', top: { xs: 16, sm: 24 }, right: { xs: 16, sm: 24 }, border: '1px solid', borderColor: 'divider', bgcolor: 'background.paper' }}
        >
          {mode === 'light' ? <DarkModeOutlinedIcon fontSize="small" /> : <LightModeOutlinedIcon fontSize="small" />}
        </IconButton>
        <Box sx={{ width: '100%', maxWidth: 440 }}>
          <Typography sx={{ color: 'primary.main', fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', fontWeight: 700 }}>Secure access</Typography>
          <Typography variant="h4" component="h1" sx={{ mt: 1.5, fontSize: { xs: 28, sm: 34 }, fontWeight: 600 }}>Welcome back.</Typography>
          <Typography color="text.secondary" sx={{ mt: 1.25, mb: { xs: 3, sm: 4 }, lineHeight: 1.65 }}>Sign in to review live operations and respond to security events.</Typography>
          {expired && !error && <Alert severity="info" sx={{ mb: 2.5 }}>Your session has ended. Sign in again to continue.</Alert>}
          {error && <Alert severity="error" sx={{ mb: 2.5 }}>{error}</Alert>}
          <Box component="form" onSubmit={handleSubmit}>
            <Stack spacing={2.25}>
              <TextField label="Email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoFocus fullWidth />
              <TextField label="Password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required fullWidth
                InputProps={{ endAdornment: <InputAdornment position="end"><IconButton aria-label={showPassword ? 'Hide password' : 'Show password'} edge="end" onClick={() => setShowPassword((value) => !value)}>{showPassword ? <VisibilityOffOutlinedIcon /> : <VisibilityOutlinedIcon />}</IconButton></InputAdornment> }} />
              <Button type="submit" variant="contained" size="large" disabled={submitting || isLoading} sx={{ mt: 1, py: 1.35 }}>{submitting ? 'Signing in…' : 'Sign in'}</Button>
            </Stack>
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 4, lineHeight: 1.6 }}>Authorized store personnel only. Contact your system administrator if you need access.</Typography>
          <Button onClick={() => navigate('/dashboard')} sx={{ mt: 2 }}>View read-only demo</Button>
        </Box>
      </Box>
    </Box>
  );
}
