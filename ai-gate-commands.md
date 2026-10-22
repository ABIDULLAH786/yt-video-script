# 1. 640 pass — must finish first, it creates the manifest
./run_family_640.sh f1_folding_quad

# 2. optional second look, only at what 640 missed
../.venv/bin/python ai_rescue1280.py --out "filtered/AI Filtered/f1_folding_quad"


# For rest of the families

./run_family_640.sh f2_micro_selfie
./run_family_640.sh f3_ducted_fpv
./run_family_640.sh f5_enterprise_quad
./run_family_640.sh f6_heavy_lift_v2
./run_family_640.sh f7_fixed_wing_vtol
