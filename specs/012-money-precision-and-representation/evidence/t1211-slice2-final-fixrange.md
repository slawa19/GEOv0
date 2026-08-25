The simulator depth-ladder change does not cover the actual execution loop and introduces an additional expensive query for long-cycle-only graphs.

Review comment:

- [P2] Apply the depth ladder inside the execution loop — C:\Users\admin\AppData\Local\Temp\geov0-t1211b-394229bbce024a04adee09692946a422\frozen\app\core\simulator\real_clearing_engine.py:186-190
  When an equivalent has only 5–6-edge cycles, this preflight now runs the short SQL query and then the full-depth query, but the `while` loop immediately overwrites `cycles` with another `find_cycles(..., max_depth=max_depth)` before executing anything. This adds an extra detector query compared with the base code, and every subsequent cleared cycle still incurs the full graph load/DFS that the ladder is intended to avoid; reuse the preflight result for the first attempt and apply the ladder to the loop's later searches.