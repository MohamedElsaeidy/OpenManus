/**
 * Sidebar toggles that do not collide.
 *
 * Collapsing the sidebar is offcanvas — it disappears entirely — so there must
 * always be a way back. Pages with a header bar put a trigger in it; pages
 * without one (home, admin, settings) rely on a floating button.
 *
 * Both in the same place at once looked like a rendering bug, so a page-level
 * trigger registers itself and the floating button steps aside while one is
 * mounted.
 */
import { SidebarTrigger, useSidebar } from '@/components/ui/sidebar';
import { cn } from '@/libs/utils';
import { PanelLeftIcon } from 'lucide-react';
import { useEffect } from 'react';
import { create } from 'zustand';

const useMountedTriggers = create<{
  count: number;
  register: () => void;
  unregister: () => void;
}>(set => ({
  count: 0,
  register: () => set(state => ({ count: state.count + 1 })),
  unregister: () => set(state => ({ count: Math.max(0, state.count - 1) })),
}));

/** Trigger for a page that has its own header to put it in. */
export const PageSidebarTrigger = ({ className }: { className?: string }) => {
  const register = useMountedTriggers(state => state.register);
  const unregister = useMountedTriggers(state => state.unregister);

  useEffect(() => {
    register();
    return unregister;
  }, [register, unregister]);

  return <SidebarTrigger className={className} title="Toggle sidebar (⌘B)" />;
};

/** Fallback for pages with no header, shown only while the sidebar is hidden. */
export const FloatingSidebarTrigger = () => {
  const { state, isMobile, toggleSidebar } = useSidebar();
  const pageTriggerCount = useMountedTriggers(triggers => triggers.count);

  if (isMobile || state !== 'collapsed' || pageTriggerCount > 0) return null;

  return (
    <button
      onClick={toggleSidebar}
      title="Show sidebar (⌘B)"
      aria-label="Show sidebar"
      className={cn(
        'bg-card hover:bg-accent hover:text-accent-foreground text-muted-foreground',
        'fixed top-3 left-3 z-50 inline-flex h-8 w-8 items-center justify-center',
        'rounded-md border shadow-sm transition-colors',
      )}
    >
      <PanelLeftIcon className="h-4 w-4" />
    </button>
  );
};
