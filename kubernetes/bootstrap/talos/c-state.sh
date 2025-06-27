#!/bin/bash

# Talos C-State Monitoring Script
# Usage: ./cstate_monitor.sh <IP_ADDRESS>

# Check if IP address is provided
if [ $# -eq 0 ]; then
    echo "Usage: $0 <IP_ADDRESS>"
    echo "Example: $0 192.168.50.34"
    exit 1
fi

IP="$1"
TALOS_CONFIG="clusterconfig/talosconfig"

echo "======================================"
echo "C-State Analysis for Node: $IP"
echo "======================================"
echo

# Function to run talosctl read command
run_talos_read() {
    talosctl -n "$IP" --talosconfig "$TALOS_CONFIG" read "$1" 2>/dev/null
}

# Check if node is reachable
echo "🔍 Checking node connectivity..."
if ! run_talos_read "/proc/version" >/dev/null; then
    echo "❌ Error: Cannot connect to node $IP"
    echo "   Please check:"
    echo "   - IP address is correct"
    echo "   - Talos config path: $TALOS_CONFIG"
    echo "   - Node is running and accessible"
    exit 1
fi
echo "✅ Node is reachable"
echo

# CPU Governor and Driver Info
echo "📊 CPU Power Management Configuration"
echo "======================================"
echo -n "CPU Frequency Governor: "
run_talos_read "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"

echo -n "CPU Idle Driver: "
run_talos_read "/sys/devices/system/cpu/cpuidle/current_driver"

echo -n "CPU Idle Governor: "
run_talos_read "/sys/devices/system/cpu/cpuidle/current_governor"

echo -n "Intel Idle Max C-State: "
run_talos_read "/sys/module/intel_idle/parameters/max_cstate"

echo -n "Processor Max C-State: "
run_talos_read "/sys/module/processor/parameters/max_cstate"
echo

# Get available C-states
echo "💤 Available C-States and Usage Statistics"
echo "==========================================="

total_cores=$(run_talos_read "/sys/devices/system/cpu/present" | awk -F'-' '{print $2+1}')
echo "Detected $total_cores CPUs"

grand_total_time=0
grand_total_usage=0
deep_states_time=0

for cpu in $(seq 0 $((total_cores-1))); do
    echo "CPU$cpu:"
    states=0
    # Detect number of states for this CPU
    for i in {0..9}; do
        if run_talos_read "/sys/devices/system/cpu/cpu${cpu}/cpuidle/state$i/name" >/dev/null 2>&1; then
            states=$((i + 1))
        else
            break
        fi
    done

    for i in $(seq 0 $((states-1))); do
        state_path="/sys/devices/system/cpu/cpu${cpu}/cpuidle/state$i"
        name=$(run_talos_read "$state_path/name")
        usage=$(run_talos_read "$state_path/usage")
        time_us=$(run_talos_read "$state_path/time")
        time_sec=$(echo "$time_us" | awk '{printf "%.2f", $1 / 1000000}' 2>/dev/null || echo "0")
        printf "  state%-2s %-8s Usage: %-12s Time: %-12s\n" "$i" "$name" "$usage" "$time_sec"

        grand_total_time=$(echo "$grand_total_time $time_sec" | awk '{printf "%.2f", $1 + $2}')
        grand_total_usage=$(echo "$grand_total_usage $usage" | awk '{printf "%.0f", $1 + $2}')
        # Deep states: C6 and deeper (i >= 2)
        if [ "$i" -ge 2 ]; then
            deep_states_time=$(echo "$deep_states_time $time_sec" | awk '{printf "%.2f", $1 + $2}')
        fi
    done
done

echo
echo "Total idle time (all CPUs): $grand_total_time s"
echo "Total deep C-state time (C6+): $deep_states_time s"
if [ "$(echo "$grand_total_time > 0" | awk '{if($1>0) print "1"; else print "0"}')" = "1" ]; then
    percent=$(echo "$deep_states_time $grand_total_time" | awk '{printf "%.1f", $1*100/$2}')
    echo "Deep C-state residency: $percent %"
fi

# Analysis section
echo "📈 Power Efficiency Analysis"
echo "============================"

# Find the deepest state being used
deepest_used=""
deepest_time=0
for i in $(seq $((states-1)) -1 0); do
    usage=$(run_talos_read "/sys/devices/system/cpu/cpu0/cpuidle/state$i/usage")
    time_us=$(run_talos_read "/sys/devices/system/cpu/cpu0/cpuidle/state$i/time")
    if [ "$usage" -gt 0 ] && [ -z "$deepest_used" ]; then
        deepest_used="state$i ($(run_talos_read "/sys/devices/system/cpu/cpu0/cpuidle/state$i/name"))"
        deepest_time=$(echo "$time_us" | awk '{printf "%.2f", $1 / 1000000}' 2>/dev/null || echo "0")
        break
    fi
done

if [ -n "$deepest_used" ]; then
    percentage=$(echo "$deepest_time $grand_total_time" | awk '{if($2>0) printf "%.1f", $1*100/$2; else print "0"}' 2>/dev/null || echo "0")
    echo "✅ Deepest C-state in use: $deepest_used"
    echo "   Time spent in deepest state: ${deepest_time}s (${percentage}% of idle time)"
else
    echo "❌ No deep C-states detected"
fi

# Check if deep states are being used effectively
deep_states_time=0
for i in $(seq 2 $((states-1))); do
    if [ $i -lt $states ]; then
        time_us=$(run_talos_read "/sys/devices/system/cpu/cpu0/cpuidle/state$i/time" 2>/dev/null || echo "0")
        time_sec=$(echo "$time_us" | awk '{printf "%.2f", $1 / 1000000}' 2>/dev/null || echo "0")
        deep_states_time=$(echo "$deep_states_time $time_sec" | awk '{printf "%.2f", $1 + $2}' 2>/dev/null || echo "$deep_states_time")
    fi
done

if [ "$(echo "$grand_total_time" | awk '{if($1>0) print "1"; else print "0"}')" = "1" ]; then
    deep_percentage=$(echo "$deep_states_time $grand_total_time" | awk '{printf "%.1f", $1*100/$2}' 2>/dev/null || echo "0")
    echo "💡 Time spent in deep C-states (C6+): ${deep_states_time}s (${deep_percentage}%)"

    # Use awk for floating point comparison
    if [ "$(echo "$deep_percentage" | awk '{if($1>50) print "1"; else print "0"}')" = "1" ]; then
        echo "🌟 Excellent power efficiency! CPU spending significant time in deep sleep states."
    elif [ "$(echo "$deep_percentage" | awk '{if($1>20) print "1"; else print "0"}')" = "1" ]; then
        echo "👍 Good power efficiency. CPU using deep sleep states regularly."
    else
        echo "⚠️  Limited deep sleep usage. Consider checking workload or power settings."
    fi
fi

echo
echo "🔄 To monitor changes over time, run this script again later and compare usage counts."
echo "💡 Higher usage counts and time in deeper states (C6, C8, C10) indicate better power efficiency."