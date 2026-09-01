"""
[DEPRECATED] Deformation report section using AJAX-based Resensys API.

This module is no longer used in the main pipeline.
It has been replaced by sensor_data.py which uses the REST API
and avoids duplicate data fetching.

Kept for reference or standalone use only.
"""
import re
import threading
import time
import requests
import pandas as pd
from bridge_info import resensys_login
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import json
import sys
import os
from pathlib import Path

from api_rate_limiter import acquire_slot, release_slot, wait_for_gap

from temp_utils import detect_and_convert_celsius

class DeformationReportSection:
    """Handles section 1.1.1 - Καταγραφές παραμόρφωσης (Deformation Records)"""
    
    def __init__(self):
        # Read bridge name from bridges.txt (relative path)
        bridges_txt = Path(__file__).parent / 'bridges.txt'
        if bridges_txt.exists():
            bridge_name = bridges_txt.read_text(encoding='utf-8').strip()
        else:
            bridge_name = ''
        print(f"Bridge name: {bridge_name}")
        self.bridge_name = bridge_name
        self.api_url = f"https://lms4.globiled.com/Bridges_api/bridges_api_new.php?code_name={bridge_name}"
        self.resensys_url = "https://greece.resensys.cloud/ajax/plot_if_active/"
        self.sensor_data = []
        self.stats_data = []
        self.acceleration_stats_data = []
        
        # Date range configuration - populated from API sensor data
        self.date_config = {
            "Finish_time": "04:11:00",
            "Pilot_Date_Start": "",
            "Pilot_Date_Finish": ""
        }
        
        # Resensys credentials and settings
        self.username = "Vassilis"
        self.session = None
        self.csrf_token = None
        # Use safe_filename to ensure folder name matches what main.py expects
        safe_bridge = re.sub(r"[^a-zA-Z0-9_\-.]+", "_", bridge_name)
        self.output_dir = Path('graphs') / safe_bridge
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def fetch_sensor_info(self):
        """Fetch sensor information from the API"""
        try:
            print("Fetching sensor information from API...")
            response = requests.get(self.api_url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            # Collect all sites - handle both flat {"sites": [...]} and nested {"regions": [...]}
            all_sites = []
            if "sites" in data:
                all_sites = data["sites"]
            elif "regions" in data:
                for region in data["regions"]:
                    for bridge in region.get("bridges", []):
                        all_sites.extend(bridge.get("sites", []))
            else:
                raise ValueError(f"Unexpected API response structure. Keys: {list(data.keys())}")

            # Extract sensor information - sensors are nested inside sites
            sensors = []
            for site in all_sites:
                site_id = site.get("site_id")
                if "sensors" in site:
                    for sensor in site["sensors"]:
                        if all(key in sensor for key in ["Code_name", "device_id"]):
                            sensor_info = {
                                "code_name": sensor["Code_name"],
                                "site_id": site_id,
                                "device_id": sensor["device_id"],
                                "type": sensor.get("type", ""),
                                "pilot_date_start": sensor.get("Pilot_Date_Start", ""),
                                "pilot_date_finish": sensor.get("Pilot_Date_Finish", ""),
                                "e0": sensor.get("e0"),
                                "t0": sensor.get("t0"),
                            }
                            sensors.append(sensor_info)
                    
            print(f"Found {len(sensors)} sensors")
            self.sensor_data = sensors
            return sensors
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching sensor data: {e}")
            return []
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error: {e}")
            return []
    
    def login_resensys(self):
        """Login to Resensys via bridge_info.resensys_login (GET /login -> POST /login/)."""
        self.session, self.csrf_token = resensys_login()
        print(f"  Login OK. csrftoken: {self.csrf_token[:10]}...")
        return True
    
    def fetch_sensor_data_from_resensys(self, sensor, measurement_type="Strain-xx-high_rate", unit="us"):
        """Fetch sensor data from Resensys API (requires login_resensys first)"""
        device_id = sensor['device_id']
        site_id = sensor['site_id']
        
        # Build time range from pilot dates
        start_date = sensor.get('pilot_date_start') or self.date_config.get('Pilot_Date_Start')
        end_date = sensor.get('pilot_date_finish') or self.date_config.get('Pilot_Date_Finish')

        if not start_date or not end_date:
            raise ValueError(f"Missing pilot dates for sensor {sensor.get('device_id')}")

        # Format dates for API
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        
        # Local time strings
        start_local = start_dt.strftime("%m/%d/%Y 00:00:00")
        end_local = end_dt.strftime("%m/%d/%Y 23:59:59")
        
        # UTC time strings (assume UTC+3 for Athens)
        start_utc = (start_dt - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        end_utc = (end_dt - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        
        form_data = {
            'username': self.username,
            'selected_DID': device_id,
            'selected_SID': site_id,
            'selected_DF': '3002',  # Data format for strain
            'selected_Q': measurement_type,
            'selected_Unit': unit,
            'selected_time_range': f"{start_local} - {end_local}",
            'selected_time_start_utc': start_utc,
            'selected_time_end_utc': end_utc,
            'local_timezone': 'Europe/Athens',
            'left_or_right': '0',
            'new_chart': '1',
            'add_chart': '1',
            'remove_avg': '1',
            'show_alert': '0',
            'median_filter': '0',
            'utc_times': '0',
            'twos_comp': '0',
            'downsample_interval': ''
        }
        
        try:
            print(f"  Fetching {measurement_type} data for {sensor['code_name']}...")
            headers = {
                'X-CSRFToken': self.csrf_token,
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': f'https://greece.resensys.cloud/graph/{site_id}/',
                'Origin': 'https://greece.resensys.cloud',
                'Accept': 'application/json, text/javascript, */*; q=0.01'
            }
            
            # Rate limiting via shared api_rate_limiter
            max_attempts = 3
            retry_sleep = 1
            last_exc = None
            data = None
            for attempt in range(max_attempts):
                try:
                    acquire_slot()
                    try:
                        wait_for_gap()
                        response = self.session.post(self.resensys_url, data=form_data, headers=headers, timeout=60, verify=False)
                    finally:
                        release_slot()

                    if response.status_code == 204:
                        print(f"    HTTP 204 (no data) for {measurement_type}")
                        return None
                    if response.status_code >= 400 and response.status_code < 500:
                        print(f"    Client error {response.status_code} (not retryable)")
                        return None

                    response.raise_for_status()
                    data = response.json()
                    break
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        print(f"    Retry {attempt + 1}/{max_attempts} for {measurement_type}: {e}")
                        time.sleep(retry_sleep)
                        retry_sleep *= 2
                    else:
                        print(f"    Error fetching data after {max_attempts} attempts: {e}")
                        return None
            
            if data is None:
                return None
            
            if 'data_page_json' not in data:
                print(f"    No data_page_json in response. Keys: {list(data.keys())}")
                return None
            
            # Parse the response
            page_data = json.loads(data['data_page_json'])
            raw_x = data.get('raw_x', [])
            raw_y = data.get('raw_y', [])
            
            if not raw_x or not raw_y:
                print(f"    Empty data arrays")
                return None
            
            # Convert timestamps (Unix epoch seconds) to datetime
            timestamps = [datetime.fromtimestamp(ts) for ts in raw_x]
            
            df = pd.DataFrame({
                'time': timestamps,
                'value': raw_y
            })
            
            print(f"    Got {len(df)} data points")
            return df
            
        except Exception as e:
            print(f"    Error fetching data: {e}")
            return None
    
    def calculate_stats(self, df):
        """Calculate statistics from sensor DataFrame"""
        if df is None or df.empty:
            return None
            
        return {
            'mean': df['value'].mean(),
            'min': df['value'].min(),
            'max': df['value'].max(),
            'std': df['value'].std(),
            'daily_variation': df['value'].max() - df['value'].min()
        }
    
    def process_sensor_data(self):
        """Process sensor data from Resensys API"""
        print("Fetching sensor data from Resensys...")
        
        stats_list = []
        temp_stats_list = []
        
        # Process strain gauge sensors
        print(f"  Sensor types found: {[s.get('type','') for s in self.sensor_data]}")
        print(f"  Sensor codes found: {[s.get('code_name','') for s in self.sensor_data]}")
        for sensor in self.sensor_data:
            sensor_type = sensor.get('type', '').lower()
            code_name = sensor.get('code_name', '').lower()
            # Accept 'strain', 'sg', or code_name containing '-sg-'
            is_strain = sensor_type in ('strain', 'sg') or '-sg-' in code_name or 'strain' in sensor_type
            if not is_strain:
                print(f"  Skipping {sensor.get('code_name','')} (type={sensor_type}) - not strain")
                continue
                
            print(f"\nProcessing {sensor['code_name']}...")
            
            # Fetch strain data
            df_strain = self.fetch_sensor_data_from_resensys(
                sensor, 
                measurement_type="Strain-xx-high_rate",
                unit="us"
            )
            
            if df_strain is not None and not df_strain.empty:
                print(f"    Strain data sample: min={df_strain['value'].min():.2f}, max={df_strain['value'].max():.2f}, mean={df_strain['value'].mean():.2f}")
                # Calculate stats
                stats = self.calculate_stats(df_strain)
                if stats:
                    stats['sensor'] = sensor['code_name']
                    stats['site_id'] = sensor['site_id']
                    stats['device_id'] = sensor['device_id']
                    stats['data'] = df_strain
                    stats['type'] = 'strain'
                    stats_list.append(stats)
                    
                    # Create strain chart
                    self.create_strain_chart(df_strain, sensor, stats)
            
            # Fetch temperature data for the same sensor
            df_temp = self.fetch_sensor_data_from_resensys(
                sensor,
                measurement_type="Internal Temperature",
                unit="F"
            )
            
            if df_temp is not None and not df_temp.empty:
                df_temp_c, detected_unit = detect_and_convert_celsius(
                    df_temp,
                    sensor_name=sensor.get("code_name", ""),
                    bridge_name=self.bridge_name,
                )
                temp_stats = self.calculate_stats(df_temp_c)
                if temp_stats:
                    temp_stats["sensor"] = sensor["code_name"]
                    temp_stats["site_id"] = sensor["site_id"]
                    temp_stats["device_id"] = sensor["device_id"]
                    temp_stats["data"] = df_temp_c
                    temp_stats["type"] = "temperature"
                    temp_stats_list.append(temp_stats)
                    self.create_temperature_chart(df_temp, sensor, temp_stats)
        
        self.stats_data = stats_list
        self.temp_stats_data = temp_stats_list
        print(f"\nProcessed {len(stats_list)} strain gauge sensors")
        print(f"Processed {len(temp_stats_list)} temperature sensors")
        
        return stats_list
    
    def generate_table(self):
        """Generate the statistics table for section 1.1.1"""
        print("\n" + "="*100)
        print("1.1.1 Καταγραφές παραμόρφωσης")
        print("="*100)
        
        if not self.stats_data:
            print("No sensor data available for table generation")
            return
        
        # Print table header
        print(f"{'Αισθητήρας':<20} {'Μέσος όρος (με)':<15} {'Ελάχιστη τιμή (με)':<18} {'Μέγιστη τιμή (με)':<18} {'Ημερήσια διακύμανση':<18}")
        print("-" * 100)
        
        # Print table rows
        for stat in self.stats_data:
            sensor_name = stat['sensor']
            mean_val = stat['mean']
            min_val = stat['min']
            max_val = stat['max']
            daily_variation = stat['daily_variation']
            
            print(f"{sensor_name:<20} {mean_val:>14.2f} {min_val:>17.2f} {max_val:>17.2f} {daily_variation:>17.2f}")
        
        print("="*100)
        print()
    
    def create_strain_chart(self, df, sensor, stats):
        """Create strain chart matching the example style"""
        fig, ax = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor('white')
        ax.set_facecolor('white')

        sensor_name = sensor['code_name']
        device_id = sensor['device_id']
        site_id = sensor['site_id']
        date_from = df['time'].min().strftime('%b %Y')
        date_to = df['time'].max().strftime('%b %Y')
        date_range = date_from if date_from == date_to else f'{date_from} – {date_to}'

        # Raw strain data - no ε0 subtraction for section 1.1.1
        values = df['value'].copy()

        # Apply rolling average (2-sample window at ~hourly sampling)
        rolling_window = 2
        values_ma = values.rolling(rolling_window, min_periods=1, center=True).mean()

        # Plot raw (grey) and rolling mean (blue)
        ax.plot(df['time'], values, color='#d1d5db', linewidth=0.8,
                label='Raw')
        ax.plot(df['time'], values_ma, color='#0066cc', linewidth=1.4,
                label='Κυλιόμενος μέσος')

        # No title - moved to caption
        # Y-axis label
        ax.set_ylabel('Strain-xx-high_rate(us)', fontsize=16)
        ax.set_xlabel('Time', fontsize=16)

        # Set tick label sizes
        ax.tick_params(axis='both', labelsize=12)
        
        # Grid
        ax.grid(True, which='major', axis='y', color='lightgray', linewidth=0.5, alpha=0.5)
        ax.grid(True, which='major', axis='x', color='lightgray', linewidth=0.5, alpha=0.5)
        ax.set_axisbelow(True)
        
        # X-axis formatting like example (shows dates like "15", "22", "Dec")
        locator = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d'))
        ax.xaxis.set_minor_locator(mdates.MonthLocator())
        ax.xaxis.set_minor_formatter(mdates.DateFormatter('%b'))
        
        ax.legend(fontsize=12, loc='best')

        # Add frame (box) around the plot
        for spine in ax.spines.values():
            spine.set_linewidth(1.2)
        
        plt.tight_layout()
        
        filename = self.output_dir / f"{sensor_name}_strain.png"
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"    Saved strain chart: {filename.name}")
    
    def create_temperature_chart(self, df, sensor, stats):
        """Create temperature chart"""
        fig, ax = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor('white')
        ax.set_facecolor('white')

        sensor_name = sensor['code_name']
        device_id = sensor['device_id']
        site_id = sensor['site_id']
        date_from = df['time'].min().strftime('%b %Y')
        date_to = df['time'].max().strftime('%b %Y')
        date_range = date_from if date_from == date_to else f'{date_from} – {date_to}'

        # Convert Fahrenheit to Celsius
        temp_celsius = (df['value'] - 32) * 5 / 9

        # Convert time to numerical values starting from 0
        time_numeric = (df['time'] - df['time'].min()).dt.total_seconds() / 86400  # Convert to days

        # Plot data
        ax.plot(time_numeric, temp_celsius, color='#0066cc', linewidth=1.2)

        # No title - moved to caption
        # Y-axis label
        ax.set_ylabel('Internal Temperature (°C)', fontsize=16)
        ax.set_xlabel('Time (days)', fontsize=16)

        # Set tick label sizes
        ax.tick_params(axis='both', labelsize=12)
        
        # Grid
        ax.grid(True, which='major', axis='y', color='lightgray', linewidth=0.5, alpha=0.5)
        ax.grid(True, which='major', axis='x', color='lightgray', linewidth=0.5, alpha=0.5)
        ax.set_axisbelow(True)
        
        # Set X-axis to start from 0
        ax.set_xlim(left=0)
        
        # Add frame (box) around the plot
        for spine in ax.spines.values():
            spine.set_linewidth(1.2)
        
        plt.tight_layout()
        
        filename = self.output_dir / f"{sensor_name}_temperature.png"
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"    Saved temperature chart: {filename.name}")
    
    def show_chart_locations(self):
        """Show where the charts were created"""
        print("\nChart Locations:")
        print("="*50)
        
        if self.output_dir.exists():
            print(f"Charts saved in: {self.output_dir.absolute()}")
            
            chart_files = list(self.output_dir.glob('*.png'))
            if chart_files:
                print(f"Found {len(chart_files)} chart files:")
                for chart in sorted(chart_files):
                    print(f"  - {chart.name}")
            else:
                print("No chart files found.")
        else:
            print("Charts directory not found.")
        
        print("="*50)
    
        
    def save_stats_csv(self):
        """Save strain stats to CSV in the output dir for later use in report tables."""
        if not self.stats_data:
            return
        rows = [
            {
                'Αισθητήρας': s['sensor'],
                'Μέσος όρος (με)': round(s['mean'], 3),
                'Ελάχιστη τιμή (με)': round(s['min'], 3),
                'Μέγιστη τιμή (με)': round(s['max'], 3),
                'Ημερήσια διακύμανση': round(s['daily_variation'], 3),
            }
            for s in self.stats_data
        ]
        df = pd.DataFrame(rows)
        # Use safe filename for CSV to match folder naming
        safe_name = re.sub(r"[^a-zA-Z0-9_\-.]+", "_", self.bridge_name)
        csv_path = self.output_dir / f"{safe_name}_strain_stats.csv"
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"    Saved stats CSV: {csv_path}")
        return csv_path

    def save_temperature_stats_to_csv(self):
        """Save temperature statistics to CSV for section 1.1.2"""
        if not hasattr(self, 'temp_stats_data') or not self.temp_stats_data:
            print("    No temperature stats data to save")
            return None
        
        rows = [
            {
                'Αισθητήρας': s['sensor'],
                'Μέσος όρος (°C)': round(s['mean'], 3),
                'Ελάχιστη τιμή (°C)': round(s['min'], 3),
                'Μέγιστη τιμή (°C)': round(s['max'], 3),
                'Ημερήσια διακύμανση (°C)': round(s['daily_variation'], 3),
            }
            for s in self.temp_stats_data
        ]
        df = pd.DataFrame(rows)
        # Use safe filename for CSV to match folder naming
        safe_name = re.sub(r"[^a-zA-Z0-9_\-.]+", "_", self.bridge_name)
        csv_path = self.output_dir / f"{safe_name}_temperature_stats.csv"
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"    Saved temperature stats CSV: {csv_path}")
        return csv_path

    def process_acceleration_data(self):
        """Process acceleration sensor data from Resensys API (HPA-Z)"""
        print("Fetching acceleration sensor data from Resensys...")
        print(f"  Total sensors: {len(self.sensor_data)}")

        # Debug: show all sensor types
        for s in self.sensor_data:
            print(f"    - {s['code_name']}: type='{s['type']}'")

        accel_stats_list = []

        # Process acceleration sensors (type contains 'AC' or 'accel')
        for sensor in self.sensor_data:
            sensor_type = sensor['type'].lower()
            code_name = sensor['code_name'].lower()
            # Check if it's an acceleration sensor (case-insensitive)
            is_accel = 'accel' in sensor_type or '-ac-' in code_name
            print(f"  Checking {sensor['code_name']}: type={sensor_type}, code={code_name}, is_accel={is_accel}")
            if not is_accel:
                continue

            print(f"\nProcessing acceleration sensor {sensor['code_name']}...")

            # Fetch HPA-Z (acceleration Z-axis) data
            df_accel = self.fetch_sensor_data_from_resensys(
                sensor,
                measurement_type="HPA-Z",
                unit="mg"
            )

            if df_accel is not None and not df_accel.empty:
                # Calculate stats
                stats = self.calculate_stats(df_accel)
                if stats:
                    stats['sensor'] = sensor['code_name']
                    stats['site_id'] = sensor['site_id']
                    stats['device_id'] = sensor['device_id']
                    stats['data'] = df_accel
                    stats['type'] = 'acceleration'
                    accel_stats_list.append(stats)

                    # Create red acceleration chart
                    self.create_acceleration_chart(df_accel, sensor, stats)

        self.acceleration_stats_data = accel_stats_list
        print(f"\nProcessed {len(accel_stats_list)} acceleration sensors")

        return accel_stats_list

    def create_acceleration_chart(self, df, sensor, stats):
        """Create acceleration chart with red color (section 1.1.2 style matching 1.2.2)"""
        from matplotlib.ticker import AutoMinorLocator, MaxNLocator

        fig, ax = plt.subplots(figsize=(12, 4), dpi=150)
        fig.patch.set_facecolor('white')
        ax.set_facecolor('white')

        sensor_name = sensor.get('code_name', '') or sensor.get('device_id', '')
        if not sensor_name:
            print(f"    ! Skipping chart: empty sensor name")
            return

        # Downsample if too many points (like section 1.2.2)
        if len(df) > 20000:
            df_plot = df.iloc[::5]
        else:
            df_plot = df

        # Scatter + line plot (like section 1.2.2)
        ax.scatter(
            df_plot['time'],
            df_plot['value'],
            s=4,
            marker='.',
            linewidths=0,
            alpha=0.9,
            color='red',
        )
        ax.plot(
            df_plot['time'],
            df_plot['value'],
            color='red',
            linewidth=0.4,
            alpha=0.6,
        )

        # No title - moved to caption
        ax.set_xlabel('Time (UTC)', fontsize=18)
        ax.set_ylabel('HPA-Z (mg)', fontsize=18)
        ax.grid(True, which='both', axis='both', alpha=0.3)

        # Add frame (box) around the plot
        for spine in ax.spines.values():
            spine.set_linewidth(1.2)

        # X-axis formatting (like section 1.2.2)
        locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

        ax.yaxis.set_major_locator(MaxNLocator(nbins=8))
        ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.tick_params(axis='both', labelsize=14)

        # Add peak annotation (like section 1.2.2)
        if not df_plot['value'].isna().all():
            peak_idx = df_plot['value'].idxmax()
            t_peak = df_plot.loc[peak_idx, 'time']
            v_peak = df_plot.loc[peak_idx, 'value']
            ax.scatter([t_peak], [v_peak], color='black', s=20, zorder=5)
            ax.annotate(
                f'Peak: {v_peak:.1f} mg',
                xy=(t_peak, v_peak),
                xytext=(0, -20),
                textcoords='offset points',
                ha='center',
                fontsize=14,
                arrowprops=dict(arrowstyle='->', lw=0.8),
            )

        fig.autofmt_xdate(rotation=0, ha='center')
        fig.tight_layout()

        filename = self.output_dir / f"{sensor_name}_acceleration.png"
        fig.savefig(filename)
        plt.close(fig)

        print(f"    Saved acceleration chart: {filename.name}")

    def generate_acceleration_table(self):
        """Generate the statistics table for section 1.1.2 acceleration"""
        print("\n" + "="*100)
        print("1.1.2 Καταγραφές επιτάχυνσης")
        print("="*100)

        if not self.acceleration_stats_data:
            print("No acceleration sensor data available for table generation")
            return

        # Print table header
        print(f"{'Αισθητήρας':<20} {'Μέσος όρος (με)':<15} {'Ελάχιστη τιμή (με)':<18} {'Μέγιστη τιμή (με)':<18} {'Ημερήσια διακύμανση':<18}")
        print("-" * 100)

        # Print table rows
        for stat in self.acceleration_stats_data:
            sensor_name = stat['sensor']
            mean_val = stat['mean']
            min_val = stat['min']
            max_val = stat['max']
            daily_variation = stat['daily_variation']

            print(f"{sensor_name:<20} {mean_val:>14.2f} {min_val:>17.2f} {max_val:>17.2f} {daily_variation:>17.2f}")

        print("="*100)
        print()

    def save_acceleration_stats_csv(self):
        """Save acceleration stats to CSV in the output dir for later use in report tables."""
        print(f"  Saving acceleration CSV... data count: {len(self.acceleration_stats_data)}")
        if not self.acceleration_stats_data:
            print("  No acceleration data to save!")
            return
        rows = [
            {
                'Αισθητήρας': s['sensor'],
                'Μέσος όρος (mg)': round(s['mean'], 3),
                'Ελάχιστη τιμή (mg)': round(s['min'], 3),
                'Μέγιστη τιμή (mg)': round(s['max'], 3),
                'Ημερήσια διακύμανση': round(s['daily_variation'], 3),
            }
            for s in self.acceleration_stats_data
        ]
        df = pd.DataFrame(rows)
        # Use safe filename for CSV to match folder naming
        safe_name = re.sub(r"[^a-zA-Z0-9_\-.]+", "_", self.bridge_name)
        csv_path = self.output_dir / f"{safe_name}_acceleration_stats.csv"
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"    Saved acceleration stats CSV: {csv_path}")
        return csv_path

    def generate_report(self):
        """Generate the complete section 1.1.1 report"""
        print("Generating Section 1.1.1 - Καταγραφές παραμόρφωσης")
        print("="*60)
        
        # Step 1: Fetch sensor information from API
        sensors = self.fetch_sensor_info()
        if not sensors:
            print("No sensors found. Exiting.")
            return
        
        # Step 2: Login to Resensys
        try:
            self.login_resensys()
        except Exception as e:
            print(f"Resensys login failed: {e}")
            return
        
        # Step 3: Process sensor data from Resensys (fetches data + creates charts)
        stats = self.process_sensor_data()
        if not stats:
            print("No sensor statistics generated. Exiting.")
            return
        
        # Step 4: Generate table
        self.generate_table()

        # Step 5: Save stats CSV
        self.save_stats_csv()

        # Step 6: Process acceleration sensor data (section 1.1.2)
        # NOTE: Acceleration data is now fetched by accel_fetcher.py
        # print("\n" + "="*60)
        # accel_stats = self.process_acceleration_data()
        # Step 7: Generate acceleration table
        # self.generate_acceleration_table()

        # Step 8: Save acceleration stats CSV
        self.save_acceleration_stats_csv()

        # Step 9: Show chart locations
        self.show_chart_locations()

        print(f"\n[OK] Section 1.1.1 report complete!")
        print(f"  - Processed {len(stats)} strain gauge sensors")
        # print(f"  - Processed {len(accel_stats)} acceleration sensors")
        print(f"  - Charts saved to: {self.output_dir}/")
        print(f"  - Date range: {self.date_config['Pilot_Date_Start']} to {self.date_config['Pilot_Date_Finish']}")


def main():
    """Main function to run the deformation report section"""
    report = DeformationReportSection()
    report.generate_report()


if __name__ == "__main__":
    main()
