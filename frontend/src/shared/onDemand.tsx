import { type ComponentType, useEffect, useState } from "react";

/**
 * A section whose code is fetched when it is first shown, for an addition to a screen that works
 * without it. Nothing is drawn until the code has arrived, and nothing at all if it cannot be fetched:
 * the screen around it is never held back and never fails with it. (`lazy` with `Suspense` would hold
 * the section back for a moment even when its code is already there, and would take the whole screen
 * down with it when the code cannot be fetched.)
 */
export function onDemand<P extends object>(load: () => Promise<{ default: ComponentType<P> }>): ComponentType<P> {
  let loaded: ComponentType<P> | null = null;
  return function OnDemand(props: P) {
    const [Section, setSection] = useState<ComponentType<P> | null>(() => loaded);
    useEffect(() => {
      if (loaded !== null) {
        return;
      }
      let wanted = true;
      load().then(
        (module) => {
          loaded = module.default;
          if (wanted) {
            setSection(() => module.default);
          }
        },
        () => undefined,
      );
      return () => {
        wanted = false;
      };
      // `load` is fixed when the section is declared.
    }, []);
    return Section === null ? null : <Section {...props} />;
  };
}
