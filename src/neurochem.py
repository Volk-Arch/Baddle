"""DEPRECATED stub после B5 W4.

Neurochem class и ProtectiveFreeze class удалены — все state и dynamics
живут в РГК (см. src/rgk.py):

    Старая методика                       Новая методика на rgk
    ─────────────────────                 ─────────────────────
    chem.dopamine/serotonin/norepin      → rgk.system.gain.value / hyst / aperture
    chem.acetylcholine/gaba              → rgk.system.plasticity.value / damping
    chem.gamma                           → rgk.gamma()
    chem.recent_rpe                      → rgk.recent_rpe
    chem.update(d, w_change, weights)    → rgk.s_graph(...)
    chem.tick_expectation()              → rgk.tick_s_pred()
    chem.record_outcome(prior, post)     → rgk.s_outcome(prior, post)
    chem.apply_to_bayes(prior, d)        → rgk.bayes_step(prior, d)
    chem.feed_acetylcholine(rate, bq)    → rgk.s_ach_feed(rate, bq)
    chem.feed_gaba(active, scattering)   → rgk.s_gaba_feed(active, scattering)
    chem.update_mode(perturb)            → rgk.system.update_mode(perturb)
    chem.to_dict() / from_dict(d)        → rgk.serialize_system() / load_system(d)

    pf.conflict_accumulator              → rgk.conflict.value
    pf.silence_pressure                  → rgk.silence_press
    pf.imbalance_pressure                → rgk.imbalance_press.value
    pf.sync_error_ema_fast/slow          → rgk.sync_fast.value / sync_slow.value
    pf.active                            → rgk.freeze_active
    pf.update(d, serotonin)              → rgk.p_conflict(d, serotonin)
    pf.feed_tick(dt, sync_err, imbal)    → rgk.p_tick(dt, sync_err, imbal)
    pf.add_silence_pressure(d)           → rgk.add_silence(d)
    pf.combined_burnout(ub)              → rgk.combined_burnout(ub)
    pf.to_dict() / from_dict(d)          → rgk.serialize_freeze() / load_freeze(d)

Файл оставлен пустым (не удалён) на случай legacy `import src.neurochem`
который где-то остался — pyflakes 0 покажет если есть. Удалится в W5/cleanup.
"""
