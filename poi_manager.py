#!/usr/bin/env python3
"""
POI Manager - Extract, store, and serve Points of Interest from KMZ/KML files.
Integrates with RF SCYTHE visualization system.
"""

import os
import re
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import logging
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('poi_manager')

# KML namespaces
KML_NS = {
    'kml': 'http://www.opengis.net/kml/2.2',
    'gx': 'http://www.google.com/kml/ext/2.2',
    'atom': 'http://www.w3.org/2005/Atom'
}


class POIManager:
    """Manages Points of Interest from KMZ/KML files with SQLite storage."""

    def __init__(self, db_path: str = 'poi_database.db'):
        """Initialize POI Manager with database connection."""
        self.db_path = db_path
        self._init_database()
        logger.info(f"POI Manager initialized with database: {db_path}")

    def _init_database(self):
        """Initialize SQLite database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create POI table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS points_of_interest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                altitude REAL DEFAULT 0,
                category TEXT DEFAULT 'general',
                source_file TEXT,
                icon_url TEXT,
                style_id TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create index for spatial queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_poi_coords
            ON points_of_interest(latitude, longitude)
        ''')

        # Create index for category filtering
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_poi_category
            ON points_of_interest(category)
        ''')

        # Create KMZ sources table to track imported files
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kmz_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                file_path TEXT,
                import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                poi_count INTEGER DEFAULT 0
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("Database schema initialized")

    def parse_kmz(self, kmz_path: str) -> List[Dict[str, Any]]:
        """
        Parse a KMZ file and extract all placemarks.

        Args:
            kmz_path: Path to the KMZ file

        Returns:
            List of POI dictionaries
        """
        pois = []

        if not os.path.exists(kmz_path):
            logger.error(f"KMZ file not found: {kmz_path}")
            return pois

        try:
            with zipfile.ZipFile(kmz_path, 'r') as kmz:
                # Find KML files in the archive
                kml_files = [f for f in kmz.namelist() if f.endswith('.kml')]

                for kml_file in kml_files:
                    kml_content = kmz.read(kml_file).decode('utf-8')
                    pois.extend(self._parse_kml_content(kml_content, kmz_path))

        except zipfile.BadZipFile:
            logger.error(f"Invalid KMZ file: {kmz_path}")
        except Exception as e:
            logger.error(f"Error parsing KMZ: {e}")

        return pois

    def parse_kml(self, kml_path: str) -> List[Dict[str, Any]]:
        """
        Parse a KML file and extract all placemarks.

        Args:
            kml_path: Path to the KML file

        Returns:
            List of POI dictionaries
        """
        if not os.path.exists(kml_path):
            logger.error(f"KML file not found: {kml_path}")
            return []

        try:
            with open(kml_path, 'r', encoding='utf-8') as f:
                kml_content = f.read()
            return self._parse_kml_content(kml_content, kml_path)
        except Exception as e:
            logger.error(f"Error parsing KML: {e}")
            return []

    def _parse_kml_content(self, kml_content: str, source_file: str) -> List[Dict[str, Any]]:
        """
        Parse KML XML content and extract placemarks.

        Args:
            kml_content: KML XML string
            source_file: Source filename for reference

        Returns:
            List of POI dictionaries
        """
        pois = []

        # KML namespace
        ns = '{http://www.opengis.net/kml/2.2}'
        gx = '{http://www.google.com/kml/ext/2.2}'

        try:
            # Parse XML
            root = ET.fromstring(kml_content)

            # Find all Placemark elements with full namespace
            placemarks = root.findall(f'.//{ns}Placemark')

            logger.info(f"Found {len(placemarks)} placemarks in {source_file}")

            for placemark in placemarks:
                poi = self._extract_placemark_data(placemark, source_file, ns, gx)
                if poi:
                    pois.append(poi)

            logger.info(f"Extracted {len(pois)} valid POIs from {source_file}")

        except ET.ParseError as e:
            logger.error(f"XML parse error: {e}")
        except Exception as e:
            logger.error(f"Error extracting placemarks: {e}")
            import traceback
            traceback.print_exc()

        return pois

    def _extract_placemark_data(self, placemark: ET.Element, source_file: str,
                                 ns: str = '{http://www.opengis.net/kml/2.2}',
                                 gx: str = '{http://www.google.com/kml/ext/2.2}') -> Optional[Dict[str, Any]]:
        """
        Extract data from a single Placemark element.

        Args:
            placemark: XML Element for the Placemark
            source_file: Source filename
            ns: KML namespace
            gx: Google extension namespace

        Returns:
            POI dictionary or None if invalid
        """
        poi = {
            'source_file': os.path.basename(source_file),
            'altitude': 0,
            'category': 'general',
            'metadata': {}
        }

        # Extract name
        name_elem = placemark.find(f'{ns}name')
        if name_elem is not None and name_elem.text:
            poi['name'] = name_elem.text.strip()
        else:
            poi['name'] = 'Unnamed POI'

        # Extract description
        desc_elem = placemark.find(f'{ns}description')
        if desc_elem is not None and desc_elem.text:
            poi['description'] = desc_elem.text.strip()
        else:
            poi['description'] = ''

        # Extract coordinates from Point
        point = placemark.find(f'.//{ns}Point')
        if point is not None:
            coords_elem = point.find(f'{ns}coordinates')
            if coords_elem is not None and coords_elem.text:
                coords = self._parse_coordinates(coords_elem.text.strip())
                if coords:
                    poi['longitude'], poi['latitude'], poi['altitude'] = coords
                    logger.debug(f"Found coordinates: {coords}")

        # If no Point coordinates, try LookAt coordinates as fallback
        if 'latitude' not in poi:
            lookat = placemark.find(f'.//{ns}LookAt')
            if lookat is not None:
                lat_elem = lookat.find(f'{ns}latitude')
                lon_elem = lookat.find(f'{ns}longitude')
                alt_elem = lookat.find(f'{ns}altitude')

                if lat_elem is not None and lon_elem is not None:
                    try:
                        poi['latitude'] = float(lat_elem.text)
                        poi['longitude'] = float(lon_elem.text)
                        poi['altitude'] = float(alt_elem.text) if alt_elem is not None else 0
                        logger.debug(f"Using LookAt coordinates: {poi['latitude']}, {poi['longitude']}")
                    except (ValueError, TypeError):
                        pass

        # Extract style reference
        style_elem = placemark.find(f'{ns}styleUrl')
        if style_elem is not None and style_elem.text:
            poi['style_id'] = style_elem.text.strip()

        # Extract LookAt for additional metadata
        lookat = placemark.find(f'.//{ns}LookAt')
        if lookat is not None:
            # Extract time span if present
            timespan = lookat.find(f'.//{gx}TimeSpan')
            if timespan is not None:
                begin = timespan.find(f'{ns}begin')
                end = timespan.find(f'{ns}end')
                if begin is not None and begin.text:
                    poi['metadata']['time_begin'] = begin.text
                if end is not None and end.text:
                    poi['metadata']['time_end'] = end.text

            # Extract view range
            range_elem = lookat.find(f'{ns}range')
            if range_elem is not None and range_elem.text:
                poi['metadata']['view_range'] = float(range_elem.text)

        # Validate required fields
        if 'latitude' not in poi or 'longitude' not in poi:
            logger.warning(f"Skipping placemark '{poi.get('name')}' - no coordinates")
            return None

        logger.info(f"Extracted POI: {poi['name']} at ({poi['latitude']}, {poi['longitude']})")
        return poi

    def _parse_coordinates(self, coords_str: str) -> Optional[Tuple[float, float, float]]:
        """
        Parse KML coordinate string (lon,lat,alt).

        Args:
            coords_str: Coordinate string in KML format

        Returns:
            Tuple of (longitude, latitude, altitude) or None
        """
        try:
            # KML format: longitude,latitude,altitude
            parts = coords_str.split(',')
            if len(parts) >= 2:
                lon = float(parts[0].strip())
                lat = float(parts[1].strip())
                alt = float(parts[2].strip()) if len(parts) > 2 else 0.0
                return (lon, lat, alt)
        except (ValueError, IndexError) as e:
            logger.warning(f"Invalid coordinates: {coords_str}")
        return None

    def import_kmz(self, kmz_path: str, category: str = 'imported') -> int:
        """
        Import POIs from a KMZ file into the database.

        Args:
            kmz_path: Path to KMZ file
            category: Category to assign to imported POIs

        Returns:
            Number of POIs imported
        """
        pois = self.parse_kmz(kmz_path)

        if not pois:
            return 0

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        imported = 0
        for poi in pois:
            poi['category'] = category
            try:
                cursor.execute('''
                    INSERT INTO points_of_interest
                    (name, description, latitude, longitude, altitude, category, source_file, style_id, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    poi.get('name'),
                    poi.get('description', ''),
                    poi.get('latitude'),
                    poi.get('longitude'),
                    poi.get('altitude', 0),
                    poi.get('category', 'general'),
                    poi.get('source_file'),
                    poi.get('style_id'),
                    json.dumps(poi.get('metadata', {}))
                ))
                imported += 1
            except sqlite3.IntegrityError as e:
                logger.warning(f"Duplicate POI skipped: {poi.get('name')}")

        # Record the source file
        cursor.execute('''
            INSERT OR REPLACE INTO kmz_sources (filename, file_path, poi_count)
            VALUES (?, ?, ?)
        ''', (os.path.basename(kmz_path), kmz_path, imported))

        conn.commit()
        conn.close()

        logger.info(f"Imported {imported} POIs from {kmz_path}")
        return imported

    def get_all_pois(self) -> List[Dict[str, Any]]:
        """Get all POIs from the database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM points_of_interest ORDER BY name')
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_pois_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get POIs filtered by category."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM points_of_interest WHERE category = ? ORDER BY name', (category,))
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_pois_in_area(self, min_lat: float, max_lat: float,
                         min_lon: float, max_lon: float) -> List[Dict[str, Any]]:
        """Get POIs within a bounding box."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM points_of_interest
            WHERE latitude BETWEEN ? AND ?
            AND longitude BETWEEN ? AND ?
            ORDER BY name
        ''', (min_lat, max_lat, min_lon, max_lon))
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_poi_count(self) -> int:
        """Get total number of POIs."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM points_of_interest')
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_categories(self) -> List[str]:
        """Get list of unique categories."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT category FROM points_of_interest ORDER BY category')
        categories = [row[0] for row in cursor.fetchall()]
        conn.close()
        return categories

    def add_poi(self, name: str, latitude: float, longitude: float,
                description: str = '', category: str = 'manual',
                altitude: float = 0, metadata: dict = None) -> int:
        """Add a single POI manually."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO points_of_interest
            (name, description, latitude, longitude, altitude, category, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, description, latitude, longitude, altitude, category,
              json.dumps(metadata or {})))

        poi_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(f"Added POI: {name} at ({latitude}, {longitude})")
        return poi_id

    def delete_poi(self, poi_id: int) -> bool:
        """Delete a POI by ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM points_of_interest WHERE id = ?', (poi_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def clear_all(self) -> int:
        """Clear all POIs from database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM points_of_interest')
        count = cursor.rowcount
        cursor.execute('DELETE FROM kmz_sources')
        conn.commit()
        conn.close()
        logger.info(f"Cleared {count} POIs from database")
        return count

    def get_visualization_data(self) -> Dict[str, Any]:
        """Get POI data formatted for Cesium visualization."""
        pois = self.get_all_pois()

        # Group by category for legend
        categories = {}
        for poi in pois:
            cat = poi.get('category', 'general')
            if cat not in categories:
                categories[cat] = 0
            categories[cat] += 1

        return {
            'pois': pois,
            'total_count': len(pois),
            'categories': categories,
            'timestamp': datetime.now().isoformat()
        }


# ============================================================================
# STANDALONE IMPORT SCRIPT
# ============================================================================

def main():
    """Main function for standalone POI import."""
    import argparse

    parser = argparse.ArgumentParser(description='Import POIs from KMZ/KML files')
    parser.add_argument('--kmz', type=str, help='Path to KMZ file to import')
    parser.add_argument('--kml', type=str, help='Path to KML file to import')
    parser.add_argument('--category', type=str, default='imported', help='Category for imported POIs')
    parser.add_argument('--db', type=str, default='poi_database.db', help='Database path')
    parser.add_argument('--list', action='store_true', help='List all POIs')
    parser.add_argument('--clear', action='store_true', help='Clear all POIs')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    manager = POIManager(db_path=args.db)

    if args.clear:
        count = manager.clear_all()
        print(f"Cleared {count} POIs")
        return

    if args.kmz:
        count = manager.import_kmz(args.kmz, category=args.category)
        print(f"Imported {count} POIs from {args.kmz}")

    if args.kml:
        pois = manager.parse_kml(args.kml)
        # Import to database
        conn = sqlite3.connect(args.db)
        cursor = conn.cursor()
        for poi in pois:
            poi['category'] = args.category
            cursor.execute('''
                INSERT INTO points_of_interest
                (name, description, latitude, longitude, altitude, category, source_file, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                poi.get('name'),
                poi.get('description', ''),
                poi.get('latitude'),
                poi.get('longitude'),
                poi.get('altitude', 0),
                poi.get('category', 'general'),
                poi.get('source_file'),
                json.dumps(poi.get('metadata', {}))
            ))
        conn.commit()
        conn.close()
        print(f"Imported {len(pois)} POIs from {args.kml}")

    if args.list:
        pois = manager.get_all_pois()
        if args.json:
            print(json.dumps(pois, indent=2, default=str))
        else:
            print(f"\n{'='*60}")
            print(f"Points of Interest ({len(pois)} total)")
            print(f"{'='*60}")
            for poi in pois:
                print(f"\n[{poi['id']}] {poi['name']}")
                print(f"    Location: {poi['latitude']:.6f}, {poi['longitude']:.6f}")
                print(f"    Category: {poi['category']}")
                if poi.get('description'):
                    print(f"    Description: {poi['description'][:50]}...")
            print(f"\n{'='*60}")


if __name__ == '__main__':
    main()
