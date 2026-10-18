"""Start ovos-core with one lock around skill loading, then run it unchanged.

ovos-core 2.1 (the testing channel) loads plugin skills from two threads at
start-up - the "internet is up" handler and the 30-second loop - without a lock,
so a slow skill can be loaded twice, and then both copies answer every question.
ovos-core 2.2.4a1 fixed it with `_plugin_skills_lock`, but that version needs the
whole alpha stack. Until testing moves on, this backports just the lock.
"""

import sys
import threading

from ovos_core import skill_manager

_lock = threading.RLock()
_load = skill_manager.SkillManager.load_plugin_skills


def load_plugin_skills(self, *args, **kwargs):
    with _lock:
        return _load(self, *args, **kwargs)


# harmless on newer cores, which hold their own (re-entrant) lock inside
skill_manager.SkillManager.load_plugin_skills = load_plugin_skills

from ovos_core.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
