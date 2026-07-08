import { motion, useScroll, useSpring, useTransform } from "framer-motion";

/** "Ride the beam" — a fixed rail showing scroll position as a traveling pulse. */
export default function ScrollBeam() {
  const { scrollYProgress } = useScroll();
  const progress = useSpring(scrollYProgress, { stiffness: 90, damping: 24 });
  const top = useTransform(progress, [0, 1], ["0%", "100%"]);

  return (
    <div
      className="fixed left-7 top-1/2 -translate-y-1/2 h-[36vh] w-px z-40 hidden xl:block"
      aria-hidden
    >
      <div className="absolute inset-0 bg-line" />
      <motion.div
        style={{ scaleY: progress }}
        className="absolute inset-0 origin-top bg-gradient-to-b from-accent/20 to-accent"
      />
      <motion.div
        style={{ top }}
        className="absolute -left-[3px] w-[7px] h-[7px] -translate-y-1/2 rounded-full bg-accent shadow-[0_0_12px_2px_color-mix(in_srgb,var(--color-accent)_60%,transparent)]"
      />
    </div>
  );
}
