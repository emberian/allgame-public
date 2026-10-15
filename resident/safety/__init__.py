"""Keeping a resident alive and its self-changes undoable.

supervisor.py   runs the resident as a child; restarts it; rolls back a
                self-edit that crashed it; stops on a crash loop
selfedit.py     edit_harness's machinery: checked, path-limited git commits
"""
