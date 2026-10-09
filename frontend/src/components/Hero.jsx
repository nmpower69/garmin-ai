import React, { useRef, useEffect, useState } from 'react'
import { motion, useReducedMotion, useScroll, useTransform } from 'framer-motion'

const POLLOUT = .03

function MagneticPill({ children, href }){
  const ref = useRef(null)
  const onMove = (e) => { if(ref.current) { const r = ref.current.getBoundingClientRect(); e.currentTarget.dataset.x = String((e.clientX - r.left - r.width/2) * 0.3) } }
  const reset = () => { if(ref.current) ref.current.dataset.x = '0' }
  return (
    <motion.a ref={ref} className="pill" href={href}
      onMouseMove={onMove} onMouseLeave={reset}
      whileTap={{scale:.97}}>
      {children}
    </motion.a>
  )
}

function HeroCopyContent({ totals }){
  const t = totals || { km:'—', hrs:'—', rides:'—', vert:'—' }
  const word = (i)=>({
    initial:{opacity:0,y:60,rotate:2},
    animate:{opacity:1,y:0,rotate:0},
    transition:{delay:.15+i*.1,type:'spring',stiffness:120,damping:16},
  })
  return (
    <>
      <motion.p className="hero-kicker" initial={{opacity:0}} animate={{opacity:1}} transition={{duration:.8}}>
        Garmin Connect <b>·</b> synced daily 10 AM IST <b>·</b> {t.rides} rides in the last 30 days
      </motion.p>
      <h1>
        <motion.span className="acid" {...word(0)} style={{display:'inline-block'}}>{t.km}</motion.span>
        {' '}<motion.span {...word(1)} style={{display:'inline-block'}}>km</motion.span>
        {' '}<motion.span {...word(2)} style={{display:'inline-block'}}>built</motion.span>
        {' '}<motion.span {...word(3)} style={{display:'inline-block'}}>to</motion.span>
        {' '}<motion.span {...word(4)} style={{display:'inline-block'}}>breathe.</motion.span>
      </h1>
      <motion.p className="hero-sub"
        initial={{opacity:0,y:20}} animate={{opacity:1,y:0}} transition={{delay:.7,duration:.7}}>
        <strong>{t.hrs} hours</strong> in the saddle · <strong>{t.vert} m</strong> climbed ·
        ridden for happiness, not racing. Everything below is your real Garmin data.
      </motion.p>
      <motion.div initial={{opacity:0,y:20}} animate={{opacity:1,y:0}} transition={{delay:.9,duration:.7}}>
        <MagneticPill href="#charts">
          See the data
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M4 12L12 4M12 4H6M12 4V10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
          </svg>
        </MagneticPill>
      </motion.div>
    </>
  )
}

export default function Hero({ totals }){
  const stageRef = useRef(null)
  const videoRef = useRef(null)
  const reduced = useReducedMotion()
  const baseRaw = import.meta.env.BASE_URL || '/'
  const base = baseRaw.endsWith('/') ? baseRaw.slice(0, -1) : baseRaw
  const asset = (p) => {
    const b = base
    const r = b ? b + '/' + p : p
    return r.replace(/\/+/g, '/')
  }
  const [ready, setReady] = useState(false)

  const { scrollYProgress } = useScroll({
    target: stageRef, offset: ['start start', 'end end']
  })

  // Map scroll progress onto video.currentTime.
  //
  // One rAF loop only. The scroll handler never schedules frames, it just
  // stores the newest target; the loop reads that target and decides whether
  // to seek. scroll.mp4 is encoded all-intra (every frame a keyframe), so a
  // seek lands on a decodable frame immediately and no seek queue builds up.
  useEffect(() => {
    if (!ready) return
    const video = videoRef.current
    if (!video) return
    let rafId = 0
    let target = -1
    let pending = false

    const tick = () => {
      if (target >= 0 && !pending) {
        // Skip no-op writes; seeking is the expensive part.
        if (Math.abs(video.currentTime - target) >= POLLOUT) {
          pending = true
          video.currentTime = target
        }
      }
      rafId = requestAnimationFrame(tick)
    }

    const onSeeked = () => { pending = false }

    const apply = (p) => {
      const d = video.duration
      if (!d || !isFinite(d)) return
      // Stop one frame short of the end so the final frame is paintable.
      target = Math.max(0, Math.min(d - POLLOUT, p * d))
    }

    const unsub = scrollYProgress.on('change', apply)
    video.addEventListener('seeked', onSeeked)
    apply(scrollYProgress.get())
    rafId = requestAnimationFrame(tick)

    return () => {
      unsub()
      cancelAnimationFrame(rafId)
      video.removeEventListener('seeked', onSeeked)
    }
  }, [ready, scrollYProgress])

  const textOpacity = useTransform(scrollYProgress, [.10, .34], [1, 0])
  const textY = useTransform(scrollYProgress, [.10, .34], [0, -50])
  const textPointer = useTransform(scrollYProgress, v => v < .34 ? 'auto' : 'none')

  // Reduced motion: no scrubbing and no sticky pin (CSS flattens the stage).
  // Show a static frame instead of the same video.
  if (reduced) {
    return (
      <div className="hero">
        <div className="hero-video-still" style={{backgroundImage:`url(${asset('rider/scroll-poster.jpg')})`, backgroundPosition:'center 45%'}}/>
        <div className="hero-inner"><HeroCopyContent totals={totals}/></div>
      </div>
    )
  }

  return (
    <div className="hero-stage" ref={stageRef}>
      <div className="hero-pinned">
        <div className="hero">
          {/* No autoplay: playback is driven entirely by scroll. */}
          <video ref={videoRef} className="hero-video"
            src={asset('rider/scroll.mp4')}
            poster={asset('rider/scroll-poster.jpg')}
            preload="auto" muted playsInline
            onLoadedMetadata={() => setReady(true)}
            onError={() => setReady(false)}
            aria-hidden="true"/>
          <div className="hero-video-tint" aria-hidden="true"/>
          <motion.div className="hero-inner" style={reduced ? null : {opacity:textOpacity, y:textY, pointerEvents:textPointer}}>
            <HeroCopyContent totals={totals}/>
          </motion.div>
          <motion.div className="scrub-hint" aria-hidden="true"
            initial={{opacity:0}} animate={{opacity:1}} transition={{delay:1.2}}>
            <span>Scroll to ride</span>
            <i/>
          </motion.div>
        </div>
      </div>
    </div>
  )
}
