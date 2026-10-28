/**
 * Animate the search area between its centered and docked positions.
 */

import { useEffect, useLayoutEffect, useRef, useState, type AnimationEvent } from 'react';

/** Measure the old and new positions, then let CSS animate the move between them. */
export function useHeroTransition(docked: boolean) {
  const ref = useRef<HTMLElement>(null);
  const previous = useRef(new Map<Element, { bounds: DOMRect; fontSize: number }>());
  const [moving, setMoving] = useState(false);

  useEffect(() => {
    // Stop marking the search as moving if reduced motion is turned on during the animation.
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const stopMotion = () => {
      if (preference.matches) setMoving(false);
    };

    preference.addEventListener('change', stopMotion);

    return () => preference.removeEventListener('change', stopMotion);
  }, []);

  function capture() {
    // Call this before changing docked to remember each child's position and text
    // size. The animation starts from these values after React changes the layout.
    setMoving(false);

    previous.current.clear();

    for (const child of Array.from(ref.current?.children ?? [])) {
      previous.current.set(child, {
        bounds: child.getBoundingClientRect(),
        fontSize: Number.parseFloat(getComputedStyle(child).fontSize),
      });
    }
  }

  useLayoutEffect(() => {
    if (!previous.current.size) return;

    // Skip the animation for reduced motion and clear the saved positions so a
    // later transition cannot accidentally reuse them.
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      previous.current.clear();
      setMoving(false);

      return;
    }

    // Measure the new positions before the browser paints, then visually move each
    // child back to its old position. CSS brings it to the new position using transforms.
    for (const child of Array.from(ref.current?.children ?? [])) {
      const before = previous.current.get(child);
      if (!before || !(child instanceof HTMLElement)) continue;

      const after = child.getBoundingClientRect();

      // The heading changes font size too, so scale it to match its old appearance.
      // The other children only need to move.
      const scale =
        child.tagName === 'H1'
          ? before.fontSize / Number.parseFloat(getComputedStyle(child).fontSize)
          : 1;

      child.style.setProperty('--hero-from-x', `${before.bounds.left - after.left}px`);
      child.style.setProperty('--hero-from-y', `${before.bounds.top - after.top}px`);
      child.style.setProperty('--hero-from-scale', String(scale));
    }

    // Clear the saved positions after using them and set data-moving to start the CSS animation.
    previous.current.clear();

    setMoving(true);
  }, [docked]);

  function finish(event: AnimationEvent<HTMLElement>) {
    // Child animation events reach this handler too. Only the form's settle
    // animation tells us the search has finished moving.
    if (event.target instanceof HTMLFormElement && event.animationName === 'hero-settle')
      setMoving(false);
  }

  return { ref, moving, capture, finish };
}
