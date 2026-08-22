import { useGSAP } from '@gsap/react';
import { Box, Divider, Typography } from '@mui/material';
import gsap from 'gsap';
import { useRef } from 'react';

export function DashboardFooter() {
  const footerRef = useRef<HTMLElement | null>(null);

  useGSAP(
    () => {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      gsap.fromTo(
        footerRef.current,
        { autoAlpha: 0, y: 8 },
        { autoAlpha: 1, y: 0, duration: 0.45, ease: 'power2.out' },
      );
    },
    { scope: footerRef },
  );

  return (
    <Box
      ref={footerRef}
      component="footer"
      sx={{
        flexShrink: 0,
        px: { xs: 2, sm: 3.5, lg: 5 },
        pb: { xs: 2.25, sm: 3 },
      }}
    >
      <Box
        sx={{
          maxWidth: 1720,
          mx: 'auto',
          pt: 2.25,
          borderTop: '1px solid',
          borderColor: 'divider',
          display: 'flex',
          flexDirection: { xs: 'column', sm: 'row' },
          alignItems: { xs: 'flex-start', sm: 'center' },
          justifyContent: 'space-between',
          gap: 1.5,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.15, minWidth: 0 }}>
          <Box
            aria-hidden="true"
            sx={{
              width: 24,
              height: 24,
              borderRadius: 1,
              bgcolor: 'primary.main',
              display: 'grid',
              placeItems: 'center',
              boxShadow: (theme) => `0 0 0 4px ${theme.palette.action.hover}`,
            }}
          >
            <Box sx={{ width: 7, height: 7, borderRadius: '50%', bgcolor: 'background.paper' }} />
          </Box>
          <Box minWidth={0}>
            <Typography sx={{ color: 'text.primary', fontSize: 12.5, fontWeight: 650, lineHeight: 1.25 }}>
              Ketch Enterprise <Box component="span" sx={{ color: 'primary.main' }}>AI</Box>
            </Typography>
            <Typography sx={{ color: 'text.secondary', fontSize: 11.25, lineHeight: 1.35 }}>
              Retail loss prevention
            </Typography>
          </Box>
        </Box>

        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: { xs: 0.9, sm: 1.25 },
            color: 'text.secondary',
          }}
        >
          <Typography sx={{ fontSize: 11.25 }}>Runs fully on-prem.</Typography>
          <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', sm: 'block' } }} />
          <Typography sx={{ fontSize: 11.25 }}>Video stays in your building.</Typography>
          <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', md: 'block' } }} />
          <Typography sx={{ width: { xs: '100%', md: 'auto' }, fontSize: 11.25, color: 'text.disabled' }}>
            © 2026 Ketch Enterprise AI
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}
