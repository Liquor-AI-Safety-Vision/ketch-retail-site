import VideocamOutlinedIcon from '@mui/icons-material/VideocamOutlined';
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded';
import GridViewOutlinedIcon from '@mui/icons-material/GridViewOutlined';
import DarkModeOutlinedIcon from '@mui/icons-material/DarkModeOutlined';
import LightModeOutlinedIcon from '@mui/icons-material/LightModeOutlined';
import LiquorOutlinedIcon from '@mui/icons-material/LiquorOutlined';
import MenuIcon from '@mui/icons-material/Menu';
import MoreHorizRoundedIcon from '@mui/icons-material/MoreHorizRounded';
import NotificationsActiveOutlinedIcon from '@mui/icons-material/NotificationsActiveOutlined';
import { AppBar, Avatar, Box, Divider, Drawer, IconButton, List, ListItemButton, ListItemIcon, ListItemText, Menu, MenuItem, Toolbar, Typography, useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { useColorMode } from '../../theme/ColorModeProvider';
import { DashboardFooter } from './DashboardFooter';

const drawerWidth = 232;

const navigation = [
  { label: 'Overview', to: '/dashboard', icon: <GridViewOutlinedIcon fontSize="small" /> },
  { label: 'Alerts', to: '/alerts', icon: <NotificationsActiveOutlinedIcon fontSize="small" /> },
  { label: 'Cameras', to: '/cameras', icon: <VideocamOutlinedIcon fontSize="small" /> },
];

export function AppShell() {
  const theme = useTheme();
  const dark = theme.palette.mode === 'dark';
  const { mode, toggleMode } = useColorMode();
  const shellColors = {
    sidebar: dark ? '#0E1512' : '#ECEFEB',
    active: dark ? '#19241F' : '#DDE4DE',
    hover: dark ? '#151F1B' : '#E5E9E5',
    activeText: theme.palette.text.primary,
    muted: theme.palette.text.secondary,
    faint: dark ? '#647169' : '#78827B',
  };
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));
  const [isMobileOpen, setIsMobileOpen] = useState(false);
  const [accountMenuAnchor, setAccountMenuAnchor] = useState<HTMLElement | null>(null);
  const { user, store, isDemo, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const drawer = (
    <Box
      component="aside"
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        bgcolor: shellColors.sidebar,
        color: shellColors.muted,
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          mx: 2.5,
          mt: 2.5,
          mb: 3.5,
        }}
      >
        <Box
          component={NavLink}
          to="/dashboard"
          onClick={() => setIsMobileOpen(false)}
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1.25,
            minWidth: 0,
            flex: 1,
            color: 'inherit',
            textDecoration: 'none',
            borderRadius: 0.75,
            transition: 'opacity 180ms ease',
            '&:hover': { opacity: 0.78 },
            '&:focus-visible': { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: 4 },
          }}
        >
          <LiquorOutlinedIcon aria-hidden="true" sx={{ flexShrink: 0, color: 'primary.main', fontSize: 24 }} />
          <Box minWidth={0}>
            <Typography
              color="text.primary"
              noWrap
              sx={{
                fontSize: 14,
                fontWeight: 700,
                letterSpacing: '0.08em',
                lineHeight: 1.2,
              }}
            >
              LIQUOR STORE
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              noWrap
              display="block"
              sx={{ mt: 0.55, maxWidth: '100%', fontSize: 12, lineHeight: 1.25, letterSpacing: 0 }}
            >
              Security dashboard
            </Typography>
          </Box>
        </Box>
        {isDesktop && (
          <IconButton onClick={toggleMode} aria-label={`Switch to ${mode === 'light' ? 'dark' : 'light'} mode`} size="small" sx={{ color: 'text.secondary', flexShrink: 0 }}>
            {mode === 'light' ? <DarkModeOutlinedIcon fontSize="small" /> : <LightModeOutlinedIcon fontSize="small" />}
          </IconButton>
        )}
      </Box>

      <List component="nav" aria-label="Main navigation" disablePadding sx={{ px: 1.25 }}>
        {navigation.map((item) => {
          const isActive = location.pathname.startsWith(item.to) || (item.to === '/dashboard' && location.pathname === '/');
          return (
            <ListItemButton
              component={NavLink}
              to={item.to}
              key={item.to}
              onClick={() => setIsMobileOpen(false)}
              sx={{
                minHeight: 46,
                borderRadius: 1.25,
                mb: 0.45,
                px: 1.25,
                color: isActive ? shellColors.activeText : shellColors.muted,
                bgcolor: isActive ? shellColors.active : 'transparent',
                transition: 'background-color 180ms ease, color 180ms ease, transform 180ms ease',
                '&:hover': {
                  bgcolor: isActive ? shellColors.active : shellColors.hover,
                  color: shellColors.activeText,
                },
                '&:active': { transform: 'translateY(1px)' },
                '&.Mui-focusVisible': { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: 1 },
              }}
            >
              <ListItemIcon
                sx={{
                  minWidth: 34,
                  color: isActive ? 'primary.main' : 'inherit',
                  transition: 'color 180ms ease',
                }}
              >
                {item.icon}
              </ListItemIcon>
              <ListItemText
                primary={item.label}
                primaryTypographyProps={{ fontSize: 14, fontWeight: isActive ? 600 : 400 }}
              />
            </ListItemButton>
          );
        })}
      </List>

      <Box component="footer" sx={{ mt: 'auto', px: 1.5, pb: 1.5 }}>
        <ListItemButton
          component={NavLink}
          to="/"
          onClick={() => setIsMobileOpen(false)}
          sx={{
            minHeight: 38,
            mb: 1.1,
            px: 0.75,
            borderRadius: 1,
            color: 'text.secondary',
            transition: 'background-color 180ms ease, color 180ms ease, transform 180ms ease',
            '&:hover': { color: 'text.primary', bgcolor: 'action.hover' },
            '&:active': { transform: 'translateX(-1px)' },
            '&.Mui-focusVisible': { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: 1 },
          }}
        >
          <ListItemIcon sx={{ minWidth: 31, color: 'inherit' }}>
            <ArrowBackRoundedIcon sx={{ fontSize: 18 }} />
          </ListItemIcon>
          <ListItemText primary="Back to website" primaryTypographyProps={{ fontSize: 14, fontWeight: 400 }} />
        </ListItemButton>
        <Divider sx={{ borderColor: 'divider', mb: 1.25 }} />
        <ListItemButton
          aria-haspopup="menu"
          aria-controls={accountMenuAnchor ? 'account-menu' : undefined}
          aria-expanded={accountMenuAnchor ? 'true' : undefined}
          onClick={(event) => setAccountMenuAnchor(event.currentTarget)}
          sx={{
            minHeight: 52,
            px: 0.75,
            py: 0.55,
            borderRadius: 1.25,
            gap: 1.1,
            color: 'text.primary',
            '&:hover': { bgcolor: 'action.hover' },
            '&.Mui-focusVisible': { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: 1 },
          }}
        >
          <Avatar
            variant="rounded"
            sx={{ width: 32, height: 32, borderRadius: 1.25, bgcolor: dark ? '#1B2922' : '#D8E2DB', color: 'primary.main', fontSize: 14, fontWeight: 700 }}
          >
            {user?.displayName.slice(0, 1).toUpperCase() ?? 'S'}
          </Avatar>
          <Box minWidth={0} sx={{ flex: 1, textAlign: 'left' }}>
            <Typography variant="body2" fontWeight={600} color="text.primary" noWrap>
              {user?.displayName ?? 'Demo visitor'}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: 12 }}>{isDemo ? 'Read-only demo' : store?.role ?? 'Signed in'}</Typography>
          </Box>
          <MoreHorizRoundedIcon aria-hidden="true" sx={{ flexShrink: 0, color: 'text.secondary', fontSize: 20 }} />
        </ListItemButton>
        <Menu
          id="account-menu"
          anchorEl={accountMenuAnchor}
          open={Boolean(accountMenuAnchor)}
          onClose={() => setAccountMenuAnchor(null)}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
          transformOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          slotProps={{
            paper: {
              variant: 'outlined',
              sx: { minWidth: 210, mb: 0.75, borderRadius: 1.5, boxShadow: dark ? '0 14px 38px rgba(0,0,0,.42)' : '0 14px 38px rgba(27,48,37,.14)' },
            },
          }}
        >
          <MenuItem
            onClick={() => {
              setAccountMenuAnchor(null);
              if (isDemo) navigate('/login');
              else void logout().finally(() => navigate('/dashboard')).catch(() => {});
            }}
            sx={{ minHeight: 42 }}
          >
            <ListItemText primary={isDemo ? 'Owner sign in' : 'Sign out'} primaryTypographyProps={{ fontSize: 14 }} />
          </MenuItem>
        </Menu>
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', minHeight: '100dvh', bgcolor: 'background.default', width: '100%', maxWidth: '100%', overflowX: 'hidden' }}>
      <Box component="nav" sx={{ width: { md: drawerWidth }, flexShrink: { md: 0 } }}>
        <Drawer
          variant="temporary"
          open={isMobileOpen}
          onClose={() => setIsMobileOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{
            display: { xs: 'block', md: 'none' },
            '& .MuiDrawer-paper': { width: drawerWidth, boxSizing: 'border-box', bgcolor: shellColors.sidebar },
          }}
        >
          {drawer}
        </Drawer>
        <Drawer
          variant="permanent"
          open
          sx={{
            display: { xs: 'none', md: 'block' },
            '& .MuiDrawer-paper': { width: drawerWidth, boxSizing: 'border-box', borderRight: '1px solid', borderColor: 'divider', bgcolor: shellColors.sidebar },
          }}
        >
          {drawer}
        </Drawer>
      </Box>
      <Box component="main" sx={{ flexGrow: 1, minWidth: 0, minHeight: '100dvh', bgcolor: 'transparent', display: 'flex', flexDirection: 'column' }}>
        {!isDesktop && (
          <AppBar position="sticky" color="transparent" elevation={0} sx={{ backdropFilter: 'blur(18px)', borderBottom: '1px solid', borderColor: 'divider', bgcolor: dark ? 'rgba(11, 16, 14, 0.9)' : 'rgba(243, 245, 242, 0.92)' }}>
            <Toolbar sx={{ minHeight: 58, px: 2 }}>
              <IconButton onClick={() => setIsMobileOpen(true)} aria-label="Open navigation menu" aria-expanded={isMobileOpen} edge="start" sx={{ color: 'text.primary', mr: 1 }}>
                <MenuIcon />
              </IconButton>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flex: 1, minWidth: 0 }}>
                <LiquorOutlinedIcon aria-hidden="true" sx={{ color: 'primary.main', fontSize: 19, flexShrink: 0 }} />
                <Typography
                  color="text.primary"
                  noWrap
                  sx={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.08em', lineHeight: 1 }}
                >
                  LIQUOR STORE
                </Typography>
              </Box>
              <IconButton onClick={toggleMode} aria-label={`Switch to ${mode === 'light' ? 'dark' : 'light'} mode`} sx={{ color: 'text.primary' }}>
                {mode === 'light' ? <DarkModeOutlinedIcon fontSize="small" /> : <LightModeOutlinedIcon fontSize="small" />}
              </IconButton>
            </Toolbar>
          </AppBar>
        )}
        <Box sx={{ flex: 1, width: '100%', p: { xs: 2, sm: 2.5 }, maxWidth: 1720, mx: 'auto' }}>
          <Outlet />
        </Box>
        <DashboardFooter />
      </Box>
    </Box>
  );
}
