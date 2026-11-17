"""The updater (studio.update): versions, how a copy was installed, and copying a new version over a folder. Also the
Updates tab's Check now (dashboard.service)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from dashboard import db, demo, service
from dashboard.tests.window import Window, plain
from studio import update


class VersionTest(unittest.TestCase):
    def test_compare(self):
        self.assertEqual(update.version_tuple("v1.2"), (1, 2, 0))
        self.assertEqual(update.version_tuple("1.10.0-beta"), (1, 10, 0))
        self.assertTrue(update.is_newer("v1.0.1", "1.0.0"))
        self.assertTrue(update.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(update.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(update.is_newer("0.9", "1.0.0"))

    def test_asset_names(self):
        self.assertEqual(update.asset_name("1.2.0", platform="win32"), "ReelForge-1.2.0-Windows.zip")
        self.assertEqual(update.asset_name("1.2.0", platform="darwin"), "ReelForge-1.2.0-macOS.zip")
        self.assertEqual(update.asset_name("1.2.0", platform="linux"), "ReelForge-1.2.0-Linux.tar.gz")

    def test_offline_check_is_calm(self):
        with mock.patch.object(update, "latest", side_effect=update.UpdateError("Couldn't reach GitHub (offline?)")):
            info = update.check()
        self.assertFalse(info["newer"])
        self.assertIn("offline", info["error"])

    def test_check_round_trips_to_a_release(self):
        rel = update.Release("9.0.0", "v9.0.0", "notes", assets={"ReelForge-9.0.0-Windows.zip": ("https://x/z", 10)})
        with mock.patch.object(update, "latest", return_value=rel):
            info = update.check()
        self.assertTrue(info["newer"])
        again = update.release_from(json.loads(json.dumps(info)))  # as the dashboard stores it
        self.assertEqual((again.version, again.assets), ("9.0.0", {"ReelForge-9.0.0-Windows.zip": ("https://x/z", 10)}))


class InstallTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.root = self.dir / "ReelForge"
        self.root.mkdir()

    def test_install_kind(self):
        self.assertEqual(update.install_kind(self.root), "zip")
        (self.root / "unins000.exe").write_bytes(b"")   # the old Windows installer's copies update the zip way too
        self.assertEqual(update.install_kind(self.root), "zip")
        (self.root / ".git").mkdir()
        self.assertEqual(update.install_kind(self.root), "git")

    def test_kept_paths(self):
        for rel in ("my-studio/studio.toml", "episodes/ep01/episode.json", "completed videos/x/x.mp4", "CLAUDE.md",
                    "dashboard/data/stats.db", "dashboard/config.toml", ".venv/Scripts/python.exe", "tools/blender/b",
                    "ui/node_modules/@opentui/core/package.json"):
            self.assertTrue(update.kept(rel), rel)
        for rel in ("dashboard/service.py", "ui/src/main.tsx", "ui/bun.lock", "studio/__init__.py", "README.md"):
            self.assertFalse(update.kept(rel), rel)

    def test_zip_update_copies_kit_files_and_leaves_yours(self):
        mine = {"my-studio/studio.toml": "my settings", "CLAUDE.md": "my instructions",
                "dashboard/config.toml": "my handles", "episodes/ep01/episode.json": "{}"}
        for rel, text in {**mine, "studio/__init__.py": 'KIT_VERSION = "1.0.0"\n', "README.md": "old",
                          "bin/reelforge.cmd": "same launcher"}.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        launcher = self.root / "bin" / "reelforge.cmd"
        before = launcher.stat().st_mtime_ns
        archive = self.dir / "ReelForge-1.1.0-Windows.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for rel, text in {"studio/__init__.py": 'KIT_VERSION = "1.1.0"\n', "README.md": "new",
                              "studio/new_module.py": "x = 1\n", "bin/reelforge.cmd": "same launcher",
                              "my-studio/studio.toml": "kit default", "CLAUDE.md": "bootstrap"}.items():
                z.writestr(f"ReelForge-1.1.0/{rel}", text)
        work = self.dir / "work"
        work.mkdir()
        update._copy_release(archive, self.root, work, lambda *a: None)
        self.assertEqual(update.read_version(self.root), "1.1.0")
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.root / "studio" / "new_module.py").exists())
        for rel, text in mine.items():
            self.assertEqual((self.root / rel).read_text(encoding="utf-8"), text, rel)
        self.assertEqual(launcher.stat().st_mtime_ns, before)  # unchanged files aren't rewritten

    def test_an_update_keeps_the_sounds_you_added_to_the_list(self):
        """assets/sfx/manifest.json is the kit's list, and docs/SOUND.md has you add an entry there for every sound
        you bring. An update that put the kit's list over it took those entries away, and the next mix stopped on
        "the cue sheet names sounds that aren't in assets/sfx/manifest.json"."""
        import json

        yours = {"_licenses": {"kenney": "CC0", "pixabay": "Pixabay Content License"},
                 "sounds": {"pop": {"file": "kenney/pop.ogg", "source": "kenney", "id": 1},
                            "hail_hit": {"file": "pixabay/hail_hit.ogg", "source": "pixabay"}}}
        kit = {"_licenses": {"kenney": "CC0 1.0", "mixkit": "Mixkit"}, "_mixkit_url": "https://example.invalid/{id}",
               "sounds": {"pop": {"file": "kenney/pop2.ogg", "source": "kenney", "id": 2},
                          "whoosh": {"file": "mixkit/mixkit_9.wav", "source": "mixkit", "id": 9}}}
        files = {"studio/__init__.py": 'KIT_VERSION = "1.0.0"\n', update.SOUNDS: json.dumps(yours, indent=2),
                 "assets/sfx/pixabay/hail_hit.ogg": "the sound itself"}
        for rel, text in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        src = self.dir / "new"
        for rel, text in {"studio/__init__.py": 'KIT_VERSION = "1.1.0"\n', update.SOUNDS: json.dumps(kit, indent=2)}.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_text(text, encoding="utf-8")
        update._copy_tree(src, self.root, lambda *a: None)
        now = json.loads((self.root / update.SOUNDS).read_text(encoding="utf-8"))
        self.assertEqual(now["sounds"], {**kit["sounds"], "hail_hit": yours["sounds"]["hail_hit"]})  # the kit's, and yours
        self.assertEqual(now["_licenses"], {**kit["_licenses"], "pixabay": "Pixabay Content License"})
        self.assertEqual(now["_mixkit_url"], kit["_mixkit_url"])
        self.assertTrue((self.root / "assets/sfx/pixabay/hail_hit.ogg").is_file())
        self.assertEqual(update.read_version(self.root), "1.1.0")
        # The same update again changes nothing, and with nothing of yours in it the kit's file goes in as it is.
        before = (self.root / update.SOUNDS).stat().st_mtime_ns
        update._copy_tree(src, self.root, lambda *a: None)
        self.assertEqual((self.root / update.SOUNDS).stat().st_mtime_ns, before)
        # (the kit's own entry as it put it there: an entry changed by hand is yours now, and stays)
        (self.root / update.SOUNDS).write_text(json.dumps({"sounds": {"pop": kit["sounds"]["pop"]}}), encoding="utf-8")
        update._copy_tree(src, self.root, lambda *a: None)
        self.assertEqual((self.root / update.SOUNDS).read_bytes(), (src / update.SOUNDS).read_bytes())
        (self.root / update.SOUNDS).write_text("{not json", encoding="utf-8")  # a broken one is replaced, after a
        update._copy_tree(src, self.root, lambda *a: None)                     # copy of it is kept
        self.assertEqual((self.root / update.SOUNDS).read_bytes(), (src / update.SOUNDS).read_bytes())
        kept = [p.read_text(encoding="utf-8") for p in (self.root / "backups").iterdir()]
        self.assertEqual([text for text in kept if text == "{not json"], ["{not json"])

    def sounds_update(self, yours: dict | str, kit: dict, record: dict | None = None, *, recorded: bool = True,
                      files_here: tuple[str, ...] = ()) -> tuple[dict, str]:
        """An update from 1.0.0 to 1.1.0 (the install line's, so that its summary and the note for the restarted
        window are there too) of a copy whose list of sounds is yours (or its text), beside the kit's as the last
        update put it in place (record: None for a copy with none yet), with these sound files in assets/sfx
        (files_here). Returns the list after it, and the summary. recorded: whether the new list is the record of the
        kit's after it (else checked by the test)."""
        import json

        files = {"studio/__init__.py": 'KIT_VERSION = "1.0.0"\n', "requirements.txt": "rich\n",
                 update.SOUNDS: yours if isinstance(yours, str) else json.dumps(yours, indent=2),
                 **{f"assets/sfx/{rel}": "a sound" for rel in files_here}}
        if record is not None:
            files[update.KIT_SOUNDS] = json.dumps(record, indent=2)
        for rel, text in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        new = self.dir / "ReelForge-1.1.0"
        for rel, text in {"studio/__init__.py": 'KIT_VERSION = "1.1.0"\n', "requirements.txt": "rich\n",
                          update.SOUNDS: json.dumps(kit, indent=2)}.items():
            (new / rel).parent.mkdir(parents=True, exist_ok=True)
            (new / rel).write_text(text, encoding="utf-8")
        with mock.patch.object(update, "ROOT", new), mock.patch.object(update, "_after"):
            said = update.move_in(self.root)
        if recorded:
            self.assertEqual((self.root / update.KIT_SOUNDS).read_bytes(), (new / update.SOUNDS).read_bytes())
        return json.loads((self.root / update.SOUNDS).read_text(encoding="utf-8")), said

    OLD_KIT = {"_licenses": {"kenney": "CC0"}, "_mixkit_url": "https://example.invalid/{id}",
               "sounds": {"pop": {"file": "kenney/pop.ogg", "source": "kenney", "id": 1}}}
    NEW_KIT = {"_licenses": {"kenney": "CC0 1.0", "pixabay": "Pixabay"}, "_mixkit_url": "https://example.invalid/{id}",
               "sounds": {"pop": {"file": "kenney/pop2.ogg", "source": "kenney", "id": 2},
                          "whoosh": {"file": "mixkit/mixkit_9.wav", "source": "mixkit", "id": 9}}}
    WHOOSH = {"file": "pixabay/my_whoosh.ogg", "source": "pixabay", "title": "my whoosh"}

    def test_a_sound_you_added_by_a_name_the_new_version_also_adds_stays_yours_and_is_said(self):
        """Only names the new list lacked were kept: a sound you had added as "whoosh" was replaced by the kit's
        new "whoosh" without a word or a copy, and your cue sheets mixed the kit's sound from then on. Your other
        keys at the top of the list went too."""
        yours = {**self.OLD_KIT, "_notes": "mine", "_licenses": {"kenney": "CC0", "pixabay": "Pixabay licence, mine"},
                 "sounds": {**self.OLD_KIT["sounds"], "whoosh": self.WHOOSH}}
        now, said = self.sounds_update(yours, self.NEW_KIT, record=self.OLD_KIT)
        self.assertEqual(now["sounds"], {"pop": self.NEW_KIT["sounds"]["pop"], "whoosh": self.WHOOSH})  # the kit's
        self.assertEqual(now["_licenses"], {"kenney": "CC0 1.0", "pixabay": "Pixabay licence, mine"})  # change lands
        self.assertEqual(now["_notes"], "mine")
        self.assertEqual(said, 'Updated ReelForge from 1.0.0 to 1.1.0. Your own sound "whoosh", and licence note '
                               '"pixabay" in assets/sfx/manifest.json are kept: the new version has some by those '
                               "names too, which are left out. To have both, give yours another name, in your cue "
                               "sheets too.")
        self.assertEqual(update.take_marker(self.root)["notes"], [said.split(" ", 6)[6]])  # the restarted window's
        self.assertFalse((self.root / "backups").exists())  # nothing was replaced, so no copy is needed

    def test_with_no_record_of_the_kits_list_yours_is_kept_in_backups_before_an_entry_of_it_is_replaced(self):
        """The first update that keeps the kit's list beside yours can't tell which entries you changed: the new
        version's go in, after a copy of yours, and the summary names them."""
        yours = {**self.OLD_KIT, "sounds": {**self.OLD_KIT["sounds"], "whoosh": self.WHOOSH,
                                            "hail": {"file": "pixabay/hail.ogg", "source": "pixabay"}}}
        before = (json.dumps(yours, indent=2)).encode("utf-8")
        now, said = self.sounds_update(yours, self.NEW_KIT)
        self.assertEqual(now["sounds"], {**self.NEW_KIT["sounds"], "hail": yours["sounds"]["hail"]})
        copies = list((self.root / "backups").glob("sounds-before-update-*.json"))
        self.assertEqual([c.read_bytes() for c in copies], [before])
        self.assertEqual(said, 'Updated ReelForge from 1.0.0 to 1.1.0. The new version\'s own sounds "pop" and '
                               '"whoosh", and licence note "kenney" took the place of what your '
                               "assets/sfx/manifest.json had by those names. If any of it was yours, your list as it "
                               f"was is in backups/{copies[0].name}.")

    def test_with_no_record_and_nowhere_to_keep_a_copy_yours_stay_and_the_next_update_asks_again(self):
        """Kept as they were, the kit's old entries were also taken for yours by every later update: the record of the
        kit's list went in all the same, they weren't the same as anything in it, and the kit's new entries never came,
        even once backups/ was put right."""
        (self.root / "backups").write_text("a file, not a folder", encoding="utf-8")
        yours = {**self.OLD_KIT, "sounds": {**self.OLD_KIT["sounds"], "whoosh": self.WHOOSH}}
        now, said = self.sounds_update(yours, self.NEW_KIT, recorded=False)
        self.assertEqual(now["sounds"], {"pop": self.OLD_KIT["sounds"]["pop"], "whoosh": self.WHOOSH})
        self.assertEqual(said, "Updated ReelForge from 1.0.0 to 1.1.0. Your assets/sfx/manifest.json keeps what it "
                               'had for sounds "pop" and "whoosh", and licence note "kenney", though the new version '
                               "has its own by those names: ReelForge couldn't keep a copy of your list in backups/ "
                               "before replacing any.")
        self.assertFalse((self.root / update.KIT_SOUNDS).exists())     # not told apart: no record yet
        (self.root / "backups").unlink()                                # put right before the next update
        now, said = self.sounds_update(json.loads((self.root / update.SOUNDS).read_text(encoding="utf-8")),
                                       self.NEW_KIT)
        self.assertEqual(now["sounds"], self.NEW_KIT["sounds"])
        [copy] = (self.root / "backups").glob("sounds-before-update-*.json")
        self.assertEqual(said, 'Updated ReelForge from 1.0.0 to 1.1.0. The new version\'s own sounds "pop" and '
                               '"whoosh", and licence note "kenney" took the place of what your '
                               "assets/sfx/manifest.json had by those names. If any of it was yours, your list as it "
                               f"was is in backups/{copy.name}.")

    def test_a_sound_the_new_version_takes_out_of_its_list_goes_unless_you_changed_it_or_its_file_is_here(self):
        """The kit's own entries follow the kit, but one it took out of its list stayed in yours for good: setup went
        on trying to download a sound the kit had withdrawn. Then it went without a word or a copy, also when its file
        was on this computer: a cue sheet of yours that used it stopped on "the cue sheet names sounds that aren't in
        assets/sfx/manifest.json", with nothing to say why."""
        zap = {"file": "mixkit/mixkit_7.wav", "source": "mixkit", "id": 7}
        ding = {"file": "mixkit/mixkit_8.wav", "source": "mixkit", "id": 8}
        rumble = {"file": "mixkit/mixkit_5.wav", "source": "mixkit", "id": 5}
        thud = {"file": "mixkit/mixkit_6.wav", "source": "mixkit", "id": 6}
        old_kit = {**self.OLD_KIT, "_licenses": {"kenney": "CC0", "mixkit": "Mixkit"},
                   "sounds": {**self.OLD_KIT["sounds"], "zap": zap, "ding": ding, "rumble": rumble, "thud": thud}}
        yours = {**old_kit, "sounds": {**old_kit["sounds"], "ding": {**ding, "title": "the one in my outros"}}}
        before = json.dumps(yours, indent=2).encode("utf-8")
        now, said = self.sounds_update(yours, self.NEW_KIT, record=old_kit,
                                       files_here=("mixkit/mixkit_5.wav", "_cache/thud.wav"))
        # zap goes; ding is yours; rumble's file and thud's converted copy are here, so cue sheets still mix them
        self.assertEqual(now["sounds"], {**self.NEW_KIT["sounds"], "ding": yours["sounds"]["ding"], "rumble": rumble,
                                         "thud": thud})
        self.assertEqual(now["_licenses"], self.NEW_KIT["_licenses"])
        [copy] = (self.root / "backups").glob("sounds-before-update-*.json")
        self.assertEqual(copy.read_bytes(), before)
        self.assertEqual(said, 'Updated ReelForge from 1.0.0 to 1.1.0. The new version no longer has sound "zap", and '
                               'licence note "mixkit", so they were taken out of your assets/sfx/manifest.json too: a '
                               "cue sheet of yours that uses them needs another. Your list as it was is in "
                               f"backups/{copy.name}.")
        # With nowhere to keep a copy first, they stay, and the record still has them as the kit's: the next update
        # takes them out, with a copy then.
        shutil.rmtree(self.root / "backups")
        (self.root / "backups").write_text("a file, not a folder", encoding="utf-8")
        now, said = self.sounds_update(yours, self.NEW_KIT, record=old_kit, recorded=False,
                                       files_here=("mixkit/mixkit_5.wav", "_cache/thud.wav"))
        self.assertEqual(now["sounds"]["zap"], zap)
        self.assertEqual(now["_licenses"]["mixkit"], "Mixkit")
        self.assertEqual(said, 'Updated ReelForge from 1.0.0 to 1.1.0. Your assets/sfx/manifest.json keeps sound '
                               '"zap", and licence note "mixkit", which the new version no longer has: ReelForge '
                               "couldn't keep a copy of your list in backups/ before taking them out.")
        (self.root / "backups").unlink()
        now, said = self.sounds_update(now, self.NEW_KIT,
                                       record=json.loads((self.root / update.KIT_SOUNDS).read_text(encoding="utf-8")),
                                       files_here=("mixkit/mixkit_5.wav", "_cache/thud.wav"))
        self.assertNotIn("zap", now["sounds"])
        self.assertNotIn("mixkit", now["_licenses"])
        self.assertIn('The new version no longer has sound "zap", and licence note "mixkit"', said)

    def test_a_list_of_yours_that_cant_be_read_is_kept_in_backups_before_the_new_one_takes_its_place(self):
        """A comma after the last entry you added (your mixes already stop on it): the new list went over yours with
        no copy and no word, and the sounds you had added were gone from it."""
        yours = {**self.OLD_KIT, "sounds": {**self.OLD_KIT["sounds"], "hail": {"file": "pixabay/hail.ogg"}}}
        broken = json.dumps(yours, indent=2).replace('"pixabay/hail.ogg"\n    }', '"pixabay/hail.ogg"\n    },')
        self.assertNotEqual(broken, json.dumps(yours, indent=2))
        now, said = self.sounds_update(broken, self.NEW_KIT, record=self.OLD_KIT)
        self.assertEqual(now, self.NEW_KIT)
        copies = list((self.root / "backups").glob("sounds-before-update-*.json"))
        self.assertEqual([c.read_text(encoding="utf-8") for c in copies], [broken])
        self.assertEqual(said, "Updated ReelForge from 1.0.0 to 1.1.0. Your assets/sfx/manifest.json couldn't be read "
                               "(Expecting property name enclosed in double quotes at line 15, column 3), so the new "
                               "version's list took its place. Your list as it was is in "
                               f"backups/{copies[0].name}: copy your own entries from there into the new one.")
        self.assertEqual(update.take_marker(self.root)["notes"], [said.split(" ", 6)[6]])  # the restarted window's

    def test_a_list_of_yours_that_cant_be_read_with_nowhere_to_keep_a_copy_stays_as_it_was(self):
        (self.root / "backups").write_text("a file, not a folder", encoding="utf-8")
        broken = '{"sounds": {"hail": {"file": "pixabay/hail.ogg"},}}'
        files = {"studio/__init__.py": 'KIT_VERSION = "1.0.0"\n', "requirements.txt": "rich\n", update.SOUNDS: broken,
                 update.KIT_SOUNDS: json.dumps(self.OLD_KIT)}
        for rel, text in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        new = self.dir / "ReelForge-1.1.0"
        for rel, text in {"studio/__init__.py": 'KIT_VERSION = "1.1.0"\n', "requirements.txt": "rich\n",
                          update.SOUNDS: json.dumps(self.NEW_KIT, indent=2)}.items():
            (new / rel).parent.mkdir(parents=True, exist_ok=True)
            (new / rel).write_text(text, encoding="utf-8")
        with mock.patch.object(update, "ROOT", new), mock.patch.object(update, "_after"):
            said = update.move_in(self.root)
        self.assertEqual((self.root / update.SOUNDS).read_text(encoding="utf-8"), broken)
        self.assertEqual((self.root / update.KIT_SOUNDS).read_text(encoding="utf-8"), json.dumps(self.OLD_KIT))
        self.assertEqual(update.read_version(self.root), "1.1.0")    # the rest of the update is in
        self.assertEqual(said, "Updated ReelForge from 1.0.0 to 1.1.0. Your assets/sfx/manifest.json can't be read "
                               "(Expecting property name enclosed in double quotes at line 1, column 50), and "
                               "ReelForge couldn't keep a copy of it in backups/, so it's left as it was, and the new "
                               "version's sounds aren't in it yet (the next update brings them). Put the mistake right "
                               "in a text editor: every mix stops on it until then.")

    def test_first_time_setup_keeps_the_kits_list_for_the_first_update(self):
        from studio import settings, sounds

        sfx = self.root / "assets" / "sfx"
        sfx.mkdir(parents=True)
        (sfx / "manifest.json").write_text('{"sounds": {}}\n', encoding="utf-8")
        record = sfx / ".kit-manifest.json"
        self.assertEqual(sounds.KIT_LIST.relative_to(sounds.SFX.parent.parent).as_posix(), update.KIT_SOUNDS)
        with mock.patch.object(sounds, "SFX", sfx), mock.patch.object(sounds, "KIT_LIST", record), \
                mock.patch.object(settings, "onboarded", lambda: True):
            sounds.note_kit_list()              # a channel set up already: some entries may be yours
            self.assertFalse(record.exists())
        with mock.patch.object(sounds, "SFX", sfx), mock.patch.object(sounds, "KIT_LIST", record), \
                mock.patch.object(settings, "onboarded", lambda: False):
            sounds.note_kit_list()
        self.assertEqual(record.read_bytes(), (sfx / "manifest.json").read_bytes())

    @unittest.skipIf(os.name == "nt", "file permissions are a macOS and Linux thing")
    def test_mac_zip_update_keeps_scripts_runnable(self):
        """ZipFile unpacks without permissions; the update gives the zip's executable files theirs back."""
        (self.root / "studio").mkdir()
        (self.root / "studio" / "__init__.py").write_text('KIT_VERSION = "1.0.0"\n', encoding="utf-8")
        (self.root / "setup.sh").write_text("old\n", encoding="utf-8")
        (self.root / "setup.sh").chmod(0o755)
        archive = self.dir / "ReelForge-1.1.0-macOS.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("ReelForge-1.1.0/studio/__init__.py", 'KIT_VERSION = "1.1.0"\n')
            z.writestr("ReelForge-1.1.0/README.md", "plain\n")
            for rel in ("setup.sh", "bin/reelforge"):
                info = zipfile.ZipInfo(f"ReelForge-1.1.0/{rel}")
                info.external_attr = 0o100755 << 16
                z.writestr(info, "#!/bin/sh\necho new\n")
        work = self.dir / "work"
        work.mkdir()
        update._copy_release(archive, self.root, work, lambda *a: None)
        for rel in ("setup.sh", "bin/reelforge"):
            self.assertTrue(os.access(self.root / rel, os.X_OK), rel)
        self.assertFalse(os.access(self.root / "README.md", os.X_OK))

    def test_something_else_is_refused(self):
        archive = self.dir / "other.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("Other/readme.txt", "hi")
        work = self.dir / "work"
        work.mkdir()
        with self.assertRaises(update.UpdateError):
            update._copy_release(archive, self.root, work, lambda *a: None)

    def test_marker_is_read_once(self):
        update._write_marker(self.root, "1.0.0", "1.1.0")
        self.assertEqual(update.take_marker(self.root)["to"], "1.1.0")
        self.assertIsNone(update.take_marker(self.root))

    def test_install_line_updates_an_old_installer_copy_in_place(self):
        """install.ps1 runs the new download's updater (--into) on a copy the old Windows installer made."""
        mine = {"my-studio/studio.toml": "my settings", "CLAUDE.md": "my instructions",
                "dashboard/data/stats.db": "my stats", "episodes/ep01/episode.json": "{}", "unins000.exe": "uninstaller",
                "unins000.dat": "its list"}
        for rel, text in {**mine, "studio/__init__.py": 'KIT_VERSION = "1.0.5"\n', "README.md": "old",
                          "requirements.txt": "rich\n"}.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        new = self.dir / "ReelForge-1.1.0"     # the download the install line unpacked
        for rel, text in {"studio/__init__.py": 'KIT_VERSION = "1.1.0"\n', "README.md": "new", "requirements.txt": "rich\n",
                          "studio/new_module.py": "x = 1\n", "my-studio/studio.toml": "kit default",
                          "CLAUDE.md": "bootstrap", "START-HERE.txt": "start"}.items():
            (new / rel).parent.mkdir(parents=True, exist_ok=True)
            (new / rel).write_text(text, encoding="utf-8")
        with mock.patch.object(update, "ROOT", new), mock.patch.object(update, "_after") as after:
            said = update.move_in(self.root)
        self.assertEqual(said, "Updated ReelForge from 1.0.5 to 1.1.0.")
        self.assertEqual(update.read_version(self.root), "1.1.0")
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.root / "studio" / "new_module.py").exists())
        for rel, text in mine.items():
            self.assertEqual((self.root / rel).read_text(encoding="utf-8"), text, rel)
        after.assert_called_once()
        marker = update.take_marker(self.root)      # the restarted window says "Updated to 1.1.0"
        self.assertEqual((marker["from"], marker["to"]), ("1.0.5", "1.1.0"))

    def test_install_line_leaves_a_folder_without_reelforge(self):
        (self.root / "notes.txt").write_text("mine", encoding="utf-8")
        with self.assertRaises(update.UpdateError):
            update.move_in(self.root)
        self.assertEqual([p.name for p in self.root.iterdir()], ["notes.txt"])


class AfterTest(unittest.TestCase):
    """What an update does once the new files are in, in a ReelForge folder of its own (the kit's studio/ and the
    dashboard's settings example), with only the window's packages left out."""

    def folder(self, *channels: str) -> Path:
        """A ReelForge folder with a channel set up for each name (the first is the first channel)."""
        import sys

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "ReelForge"
        kit = Path(update.__file__).resolve().parent.parent
        shutil.copytree(kit / "studio", root / "studio", ignore=shutil.ignore_patterns("__pycache__"))
        (root / "dashboard").mkdir()
        shutil.copy(kit / "dashboard" / "config.example.toml", root / "dashboard")
        env = {k: v for k, v in os.environ.items() if k != "REELFORGE_PROFILE"}
        for i, name in enumerate(channels or ("Night Physics",)):
            answers = Path(tmp.name) / "answers.json"
            answers.write_text(json.dumps({"channel_name": name, "about": "What-ifs.", "assistant": "claude",
                                           "background_stats": False}), encoding="utf-8")
            made = subprocess.run([sys.executable, "-m", "studio", "channel", *(["--new"] if i else []), "--answers",
                                   str(answers)], cwd=root, env={**env, "STUDIO_DRY_SYSTEM": "1"},
                                  capture_output=True, text=True)
            self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        return root

    def finish(self, root: Path, logged: list[str], *more) -> str:
        """The update's last steps in root, with this Python as its own and the window's packages left out (more:
        other patches); returns what it says."""
        import sys

        real = subprocess.run

        def run(cmd, *a, **k):
            if "studio.ui" in cmd:      # the window's packages (Bun): not this test's
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real(cmd, *a, **k)

        with contextlib.ExitStack() as stack:
            for patch in (mock.patch.object(update.subprocess, "run", run),
                          mock.patch.object(update, "_venv_python", lambda r: sys.executable),
                          mock.patch.object(update, "_packages", return_value=True),
                          mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}), *more):
                stack.enter_context(patch)
            return update._finish(root, "", "1.0.0", "1.1.0", lambda *a: None, logged.append)

    def test_a_mistake_in_a_channels_settings_is_said_when_the_update_is_done(self):
        """The update writes every channel's notes again, and with a typo in a studio.toml they go by its answers
        instead. What was said about that went nowhere: the update, and the window it restarted, said nothing."""
        root = self.folder()
        mine = root / "my-studio" / "studio.toml"
        typo = mine.read_text(encoding="utf-8") + "\nthis is = = a typo\n"
        mine.write_text(typo, encoding="utf-8")
        logged = []
        summary = self.finish(root, logged)
        notes = json.loads((root / "dashboard" / "data" / "updated.json").read_text(encoding="utf-8"))["notes"]
        self.assertEqual(len(notes), 1, notes)
        # (The file as the update found it: on macOS the temporary folder is /private/var/..., not /var/....)
        self.assertTrue(notes[0].startswith(f"Night Physics's settings file, {mine.resolve()}, has a mistake in it ("),
                        notes)
        self.assertIn("so your AI's notes go by its answers instead", notes[0])
        self.assertEqual(summary, f"Updated ReelForge from 1.0.0 to 1.1.0. {notes[0]}")
        self.assertEqual(logged, [])
        self.assertEqual(mine.read_text(encoding="utf-8"), typo)         # yours to fix: the update leaves it
        self.assertFalse(list(Path(tempfile.gettempdir()).glob("reelforge-said-*")))

    def test_a_temporary_file_that_cant_be_made_or_removed_never_fails_an_update_that_has_finished(self):
        """Where the notes' run says what it has to say is a temporary file. With no temporary folder to write to, or
        the file held open by a scanner on Windows when it's removed again, the update failed after every new file
        was in place: "Update again to finish it", with no newer version to offer, and the note the restarted window
        reads was never written."""
        root = self.folder()
        claude = root / "CLAUDE.md"
        real_unlink = Path.unlink
        held: list[Path] = []

        def unlink(path, missing_ok=False):
            if path.name.startswith("reelforge-said-"):
                held.append(path)
                raise PermissionError(13, "held by another program", str(path))
            return real_unlink(path, missing_ok=missing_ok)

        for what, patch in (("no temporary folder", mock.patch.object(
                                update.tempfile, "mkstemp", side_effect=PermissionError(13, "can't write there"))),
                            ("a file held open", mock.patch.object(Path, "unlink", unlink))):
            with self.subTest(what=what):
                claude.write_text("the old version's notes\n", encoding="utf-8")
                logged = []
                self.assertEqual(self.finish(root, logged, patch), "Updated ReelForge from 1.0.0 to 1.1.0.")
                self.assertEqual(logged, [])
                marker = update.take_marker(root)       # the restarted window says "Updated to 1.1.0"
                self.assertEqual((marker["from"], marker["to"]), ("1.0.0", "1.1.0"))
                self.assertIn("generated by ReelForge", claude.read_text(encoding="utf-8"))   # written all the same
        self.assertEqual(len(held), 1, held)
        real_unlink(held[0], missing_ok=True)

    def test_a_channel_whose_notes_couldnt_be_written_again_is_said_when_the_update_is_done(self):
        """The run that writes every channel's notes again printed a line for a channel whose notes couldn't be
        written, which went nowhere: with another channel's written, the update didn't fail, and that channel's notes
        kept the old version's text without a word. Answers that need fixing left them so without even that line."""
        root = self.folder("Night Physics", "Quiet One", "Tiny Worlds")
        quiet, tiny = root / "profiles" / "quiet-one", root / "profiles" / "tiny-worlds"
        (quiet / "CLAUDE.md").unlink()
        (quiet / "CLAUDE.md").mkdir()       # where its notes go, something they can't take the place of
        answers = json.loads((tiny / "my-studio" / "answers.json").read_text(encoding="utf-8"))
        (tiny / "my-studio" / "answers.json").write_text(json.dumps({**answers, "cadence": "now and then"}),
                                                         encoding="utf-8")
        logged = []
        summary = self.finish(root, logged)
        notes = json.loads((root / "dashboard" / "data" / "updated.json").read_text(encoding="utf-8"))["notes"]
        self.assertEqual(len(notes), 2, notes)
        quiet_line, tiny_line = sorted(notes)
        self.assertTrue(quiet_line.startswith("Quiet One's notes for your AI couldn't be written again ("), notes)
        self.assertTrue(quiet_line.endswith("). Once that's put right, run: reelforge -p quiet-one channel "
                                            "--regenerate --preserve-settings"), notes)
        self.assertEqual(tiny_line, "Tiny Worlds's notes for your AI couldn't be written again (its answers need "
                                    "fixing first: cadence: pick one of: 2/day, 1/day, 3/week, 1/week, ask). Once "
                                    "that's put right, run: reelforge -p tiny-worlds channel --regenerate "
                                    "--preserve-settings")
        self.assertEqual(summary, " ".join(["Updated ReelForge from 1.0.0 to 1.1.0.", *notes]))
        self.assertEqual(logged, [])        # the first channel's notes were written: the run itself went through
        self.assertIn("**Quiet One**", (root / "CLAUDE.md").read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("git"), "needs git")
class GitPullTest(unittest.TestCase):
    """A git clone that onboarding has written its CLAUDE.md and AGENTS.md into still updates."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.kit = self.dir / "kit"
        self.kit.mkdir()
        self.git(self.kit, "init", "-q")
        self.commit({"CLAUDE.md": "placeholder\n", "AGENTS.md": "placeholder\n", "README.md": "1.0\n"})
        self.git(self.dir, "clone", "-q", str(self.kit), "copy")
        self.copy = self.dir / "copy"

    def git(self, where: Path, *args: str) -> None:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, env=env)

    def commit(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            (self.kit / rel).write_text(text, encoding="utf-8")
        self.git(self.kit, "add", "-A")
        self.git(self.kit, "commit", "-q", "-m", "kit")

    def test_onboarded_files_are_kept(self):
        (self.copy / "CLAUDE.md").write_text("my channel\n", encoding="utf-8")
        (self.copy / "AGENTS.md").write_text("my channel\n", encoding="utf-8")
        self.commit({"README.md": "1.1\n", "CLAUDE.md": "new placeholder\n"})
        update._git_pull(self.copy, lambda *a: None)
        self.assertEqual((self.copy / "README.md").read_text(encoding="utf-8"), "1.1\n")
        self.assertEqual((self.copy / "CLAUDE.md").read_text(encoding="utf-8"), "my channel\n")
        self.assertEqual((self.copy / "AGENTS.md").read_text(encoding="utf-8"), "my channel\n")

    def test_codex_settings_you_edited_dont_stop_it_and_stay(self):
        (self.kit / ".codex").mkdir()
        self.commit({".codex/config.toml": 'model_reasoning_effort = "high"\n'})
        self.git(self.copy, "pull", "-q")
        (self.copy / ".codex" / "config.toml").write_text('model_reasoning_effort = "xhigh"\n', encoding="utf-8")
        self.commit({"README.md": "1.1\n"})
        update._git_pull(self.copy, lambda *a: None)                # "You've changed files in this copy", before
        self.assertEqual((self.copy / "README.md").read_text(encoding="utf-8"), "1.1\n")
        self.assertEqual((self.copy / ".codex" / "config.toml").read_text(encoding="utf-8"),
                         'model_reasoning_effort = "xhigh"\n')

    def test_other_changes_stop_it(self):
        (self.copy / "README.md").write_text("edited\n", encoding="utf-8")
        self.commit({"README.md": "1.1\n"})
        with self.assertRaises(update.UpdateError):
            update._git_pull(self.copy, lambda *a: None)
        self.assertEqual((self.copy / "README.md").read_text(encoding="utf-8"), "edited\n")


class WhatsNewTest(unittest.TestCase):
    NOTES = Path(__file__).resolve().parents[2] / ".github" / "release-notes.md"

    def test_the_updates_tab_shows_only_whats_new(self):
        from dashboard.service import whats_new

        shown = whats_new(self.NOTES.read_text(encoding="utf-8"))
        self.assertTrue(shown.startswith("## What's new in "))
        for gone in ("## Download", "| Your computer", "Already have ReelForge?", "---"):
            self.assertNotIn(gone, shown)
        self.assertEqual(whats_new("- one\r\n\r\nAlready have ReelForge? Update.\r\n---\r\nguide"), "- one")
        self.assertEqual(whats_new(""), "")

    def test_whats_new_is_short_and_says_first_time_setup(self):
        shown = self.NOTES.read_text(encoding="utf-8").split("\n---")[0]
        for word in ("onboarding", "ultracode", "advanced settings"):
            self.assertNotIn(word, shown.lower())
        for line in shown.splitlines():
            # Each bullet on one line, a bold lead first: the Updates tab before 1.0.6 (which people read these notes
            # in until they've updated) shows a bullet's wrapped lines as loose lines at the left edge.
            self.assertRegex(line, r"^(|## What's new in .+|- \*\*[^*]+\*\* .+|  - \S.+|Already have ReelForge\? .+)$")
            self.assertLessEqual(len(line), 140, line)


class CheckNowTest(unittest.TestCase):
    """Check now on the Updates tab: "Checking…" goes when the check is over, however it ends."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        db.init(Path(tmp.name) / "stats.db")
        self.w = Window({"refresh_minutes": 0, "videos_dir": str(Path(tmp.name) / "videos")}, demo.platforms())
        self.addCleanup(self.w.close)
        self.w.svc.demo = False  # the demo's Check now does nothing
        self.w.forget()

    def check(self, answer) -> list:
        with mock.patch.object(service.kit_update, "check", side_effect=answer), \
                contextlib.redirect_stderr(io.StringIO()):
            self.w.call("check_updates")
            self.assertTrue(self.w.until(lambda: len(self.w.sent("update_step")) > 1))
        return self.w.sent("update_step")

    def test_the_restarted_window_says_what_the_update_had_to_say_about_your_sounds(self):
        """The update's summary said it for a moment before the restart, and the restarted window said only the
        version."""
        note = 'Your own sound "whoosh" in assets/sfx/manifest.json is kept: the new version has one by that name too.'
        marker = {"from": "1.0.0", "to": "1.1.0", "notes": [note]}
        with mock.patch.object(service.kit_update, "take_marker", lambda: marker), \
                mock.patch.object(service.kit_update, "check", lambda: {"error": "offline", "newer": False}):
            self.w.call("start_updates")
        toast = [t for t in self.w.sent("toast") if t["title"] == "Updated"]
        self.assertEqual([(t["body"], t["severity"]) for t in toast],
                         [(f"You're on ReelForge 1.1.0 now (from 1.0.0). {note}", "warning")])
        from dashboard import notify

        self.assertIn(note, [n["body"] for n in notify.recent() if n["kind"] == "update"][0])

    def test_the_answer_replaces_checking(self):
        info = {"current": "1.0.0", "latest": "1.0.0", "newer": False, "notes": "", "url": "", "error": ""}
        steps = self.check(lambda: dict(info))
        self.assertEqual(plain(steps[0]["status"]), "Checking…")
        self.assertEqual(steps[-1], {})  # nothing over the tab's own status line any more
        self.assertIn("the newest version", plain(self.w.view("update")["status"]))

    def test_a_check_that_raises_clears_it_too(self):
        steps = self.check(RuntimeError("no such thing"))
        self.assertEqual(steps[-1], {})

    def test_an_update_under_way_keeps_its_progress_line(self):
        self.w.svc.updating = True
        self.w.svc.update_progress("Downloading", 1, 2)
        self.w.svc.after_update_check({"current": "1.0.0", "latest": "1.1.0", "newer": True, "error": ""}, False)
        self.assertIn("Downloading", plain(self.w.view("update_step")["step"]))


if __name__ == "__main__":
    unittest.main()
