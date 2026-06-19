/**
 * RF_SCYTHE Network Infrastructure Visualization Module
 * Enhanced with CUDA NeRF renderer and RF processor integration
 */

// Initialize namespace
window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * Compute the quaternion that rotates unit vector `from` onto unit vector `to`.
 * Cesium has no built-in fromUnitVectors (that's a Three.js API).
 */
RF_SCYTHE._quaternionFromUnitVectors = function(from, to, result) {
    const dot = Cesium.Cartesian3.dot(from, to);
    if (dot >= 1.0 - Cesium.Math.EPSILON6) {
        return Cesium.Quaternion.clone(Cesium.Quaternion.IDENTITY, result);
    }
    var axis;
    if (dot <= -1.0 + Cesium.Math.EPSILON6) {
        // Anti-parallel: pick any perpendicular axis for 180° rotation
        var perp = (Math.abs(from.x) < 0.9)
            ? Cesium.Cartesian3.UNIT_X
            : Cesium.Cartesian3.UNIT_Y;
        axis = Cesium.Cartesian3.normalize(
            Cesium.Cartesian3.cross(from, perp, new Cesium.Cartesian3()),
            new Cesium.Cartesian3()
        );
        return Cesium.Quaternion.fromAxisAngle(axis, Math.PI, result);
    }
    axis = Cesium.Cartesian3.normalize(
        Cesium.Cartesian3.cross(from, to, new Cesium.Cartesian3()),
        new Cesium.Cartesian3()
    );
    var angle = Math.acos(Cesium.Math.clamp(dot, -1.0, 1.0));
    return Cesium.Quaternion.fromAxisAngle(axis, angle, result);
};

RF_SCYTHE.sanitizeCoordinates = function(latitude, longitude, altitude) {
    const lat = Math.max(-90, Math.min(90, parseFloat(latitude) || 0));
    const lng = Math.max(-180, Math.min(180, parseFloat(longitude) || 0));
    const alt = Math.max(0, parseFloat(altitude) || 0);
    
    if (isNaN(lat) || isNaN(lng) || isNaN(alt)) {
        throw new Error('Invalid coordinate values');
    }
    
    return {
        latitude: lat,
        longitude: lng,
        altitude: alt
    };
};

RF_SCYTHE.NetworkInfrastructure = function(viewer) {
    this.viewer = viewer;
    this.entities = {
        cables: [],
        satellites: [],
        towers: [],
        servers: []
    };
    this.visible = {
        cables: true,
        satellites: true,
        towers: true,
        servers: true
    };
    
    this.colors = {
        cable: Cesium.Color.CYAN,
        satellite: Cesium.Color.YELLOW,
        tower: Cesium.Color.ORANGE,
        server: Cesium.Color.RED,
        connection: Cesium.Color.GREEN
    };
    
    this._setupEventHandlers();
};

RF_SCYTHE.NetworkInfrastructure.prototype._setupEventHandlers = function() {
    const handler = new Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
    const self = this;
    
    // Mouse hover for entity tooltips
    handler.setInputAction(function(movement) {
        const pickedObject = self.viewer.scene.pick(movement.endPosition);
        
        if (Cesium.defined(pickedObject) && Cesium.defined(pickedObject.id) &&
            pickedObject.id.networkInfraType) {
            
            if (self.hoveredEntity !== pickedObject.id) {
                
                if (self.hoveredEntity) {
                    self._restoreEntityAppearance(self.hoveredEntity);
                }
                
                self.hoveredEntity = pickedObject.id;
                self._highlightEntity(pickedObject.id);
                self._showTooltip(pickedObject.id, movement.endPosition);
            }
        } else {
            if (self.hoveredEntity) {
                self._restoreEntityAppearance(self.hoveredEntity);
                self.hoveredEntity = null;
                self._hideTooltip();
            }
        }
    }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
    
    // Entity click for detailed info
    handler.setInputAction(function(click) {
        const pickedObject = self.viewer.scene.pick(click.position);
        
        if (Cesium.defined(pickedObject) && Cesium.defined(pickedObject.id) &&
            pickedObject.id.networkInfraType) {
            
            self._showDetailedInfo(pickedObject.id);
        }
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
};

/**
 * Satellite helper functions for RF visualization
 */
RF_SCYTHE.NetworkInfrastructure.prototype._getSatelliteIcon = function(satellite) {
    const riskLevel = satellite.algorithmicRisk || 0;
    
    if (satellite.type === 'Military' || riskLevel > 0.8) {
        return 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMzIiIGhlaWdodD0iMzIiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMTIgMmMtMSAwLTIgLjMtMi44IC45TDcgNS41bC0xLjUuNWMtLjYuMi0xIC44LTEgMS40djJjMCAuNi40IDEuMiAxIDEuNGwxLjUuNUw5IDEzLjZjLjguNiAxLjguOSAyLjguOXMyLS4zIDIuOC0uOUwxNyAxMS4zbDEuNS0uNWMuNi0uMiAxLS44IDEtMS40di0yYzAtLjYtLjQtMS4yLTEtMS40TDE3IDUuNSAxNC44IDIuOUMxNCAyLjMgMTMgMiAxMiAyem0wIDJjLjMgMCAuNi4xLjguM0wxNSA2LjVsMS4yLjRjLjIuMDUuMy4yLjMuNHYuNmMwIC4yLS4xLjM1LS4zLjRMMTUgOC43bC0yLjIgMS44Yy0uMi4yLS41LjMtLjguMy0uMyAwLS42LS4xLS44LS4zTDkgOC43bC0xLjItLjRDNy42IDguMTUgNy41IDggNy41IDcuOHYtLjZjMC0uMi4xLS4zNS4zLS40TDkgNi41bDIuMi0xLjhjLjItLjIuNS0uMy44LS4zeiIgZmlsbD0iI2ZmNDQ0NCIvPjwvc3ZnPg==';
    } else if (satellite.type === 'Navigation') {
        return 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMzIiIGhlaWdodD0iMzIiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMTIgMmMtMSAwLTIgLjMtMi44IC45TDcgNS41bC0xLjUuNWMtLjYuMi0xIC44LTEgMS40djJjMCAuNi40IDEuMiAxIDEuNGwxLjUuNUw5IDEzLjZjLjguNiAxLjguOSAyLjguOXMyLS4zIDIuOC0uOUwxNyAxMS4zbDEuNS0uNWMuNi0uMiAxLS44IDEtMS40di0yYzAtLjYtLjQtMS4yLTEtMS40TDE3IDUuNSAxNC44IDIuOUMxNCAyLjMgMTMgMiAxMiAyem0wIDJjLjMgMCAuNi4xLjguM0wxNSA2LjVsMS4yLjRjLjIuMDUuMy4yLjMuNHYuNmMwIC4yLS4xLjM1LS4zLjRMMTUgOC43bC0yLjIgMS44Yy0uMi4yLS41LjMtLjguMy0uMyAwLS42LS4xLS44LS4zTDkgOC43bC0xLjItLjRDNy42IDguMTUgNy41IDggNy41IDcuOHYtLjZjMC0uMi4xLS4zNS4zLS40TDkgNi41bDIuMi0xLjhjLjItLjIuNS0uMy44LS4zeiIgZmlsbD0iIzQ0ZmZmZiIvPjwvc3ZnPg==';
    } else {
        return 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMzIiIGhlaWdodD0iMzIiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMTIgMmMtMSAwLTIgLjMtMi44IC45TDcgNS41bC0xLjUuNWMtLjYuMi0xIC44LTEgMS40djJjMCAuNi40IDEuMiAxIDEuNGwxLjUuNUw5IDEzLjZjLjguNiAxLjguOSAyLjguOXMyLS4zIDIuOC0uOUwxNyAxMS4zbDEuNS0uNWMuNi0uMiAxLS44IDEtMS40di0yYzAtLjYtLjQtMS4yLTEtMS40TDE3IDUuNSAxNC44IDIuOUMxNCAyLjMgMTMgMiAxMiAyem0wIDJjLjMgMCAuNi4xLjguM0wxNSA2LjVsMS4yLjRjLjIuMDUuMy4yLjMuNHYuNmMwIC4yLS4xLjM1LS4zLjRMMTUgOC43bC0yLjIgMS44Yy0uMi4yLS41LjMtLjguMy0uMyAwLS42LS4xLS44LS4zTDkgOC43bC0xLjItLjRDNy42IDguMTUgNy41IDggNy41IDcuOHYtLjZjMC0uMi4xLS4zNS4zLS40TDkgNi41bDIuMi0xLjhjLjItLjIuNS0uMy44LS4zeiIgZmlsbD0iIzQ0ZmY0NCIvPjwvc3ZnPg==';
    }
};

RF_SCYTHE.NetworkInfrastructure.prototype._getSatelliteScale = function(satellite) {
    const baseScale = 1.2;
    const signalStrength = satellite.rfSignalStrength || 50;
    const scaleFactor = 1 + (signalStrength / 100) * 0.3;
    return baseScale * scaleFactor;
};

RF_SCYTHE.NetworkInfrastructure.prototype._getSatelliteColor = function(satellite) {
    const riskLevel = satellite.algorithmicRisk || 0;
    
    if (riskLevel > 0.8) {
        return Cesium.Color.fromCssColorString('#ff4444');
    } else if (riskLevel > 0.5) {
        return Cesium.Color.fromCssColorString('#ffaa44');
    } else {
        return Cesium.Color.fromCssColorString('#44ff44');
    }
};

RF_SCYTHE.NetworkInfrastructure.prototype._getSatelliteLabel = function(satellite) {
    const signalStrength = Math.round(satellite.rfSignalStrength || 50);
    const dataFlow = Math.round((satellite.dataFlowRate || 1000) / 1000);
    const riskLevel = Math.round((satellite.algorithmicRisk || 0) * 100);
    
    let label = satellite.name;
    
    if (riskLevel > 70) {
        label += ` ⚠️${riskLevel}%`;
    }
    
    label += `\n📶${signalStrength}% 📊${dataFlow}Gb/s`;
    
    return label;
};

RF_SCYTHE.NetworkInfrastructure.prototype._getSatelliteLabelColor = function(satellite) {
    const riskLevel = satellite.algorithmicRisk || 0;
    
    if (riskLevel > 0.8) {
        return Cesium.Color.fromCssColorString('#ff4444');
    } else if (riskLevel > 0.5) {
        return Cesium.Color.fromCssColorString('#ffaa00');
    } else {
        return Cesium.Color.fromCssColorString('#00ffaa');
    }
};

/**
 * Create a Doppler-shifted RF cone for a satellite
 * @private
 */
RF_SCYTHE.NetworkInfrastructure.prototype._createDopplerCone = function(satellite, entity) {
    const altitude = satellite.altitude * 1000 || 500000;
    const velocity = satellite.velocity || [7000, 0, 0]; // m/s, default LEO velocity
    const speed = Math.sqrt(velocity[0]**2 + velocity[1]**2 + velocity[2]**2);
    
    // Doppler shift color mapping: Blue-shift (approaching), Red-shift (receding)
    // For visualization, we use velocity relative to a fixed point or just motion direction
    const dopplerColor = (velocity[0] > 0) ? 
        Cesium.Color.fromCssColorString('rgba(68, 170, 255, 0.15)') : // Blue-shift
        Cesium.Color.fromCssColorString('rgba(255, 68, 68, 0.15)');   // Red-shift
    
    return {
        length: altitude,
        topRadius: 0.0,
        bottomRadius: altitude * 0.4, // Wide footprint
        material: new Cesium.ColorMaterialProperty(dopplerColor),
        outline: true,
        outlineColor: dopplerColor.withAlpha(0.3),
        numberOfVerticalLines: 8,
        heightReference: Cesium.HeightReference.NONE
    };
};

/**
 * Add satellites to the visualization with minimal visual interference
 * Enhanced with Doppler-shifted RF cones and grouping logic
 */
RF_SCYTHE.NetworkInfrastructure.prototype.addSatellites = function(satellites) {
    try {
        if (!satellites || !Array.isArray(satellites)) {
            console.warn('Invalid satellites data');
            return;
        }
        
        // Grouping logic for large constellations (e.g. Starlink)
        const groups = {};
        const maxUngrouped = 100;
        
        if (satellites.length > maxUngrouped) {
            satellites.forEach(s => {
                const groupKey = s.operator || 'Unknown';
                if (!groups[groupKey]) groups[groupKey] = [];
                groups[groupKey].push(s);
            });
        }
        
        for (const satellite of satellites) {
            if (!satellite.name || !satellite.position) {
                console.warn('Invalid satellite data:', satellite);
                continue;
            }
            
            const position = Cesium.Cartesian3.fromDegrees(
                satellite.position[0], 
                satellite.position[1], 
                satellite.altitude * 1000 || 500000
            );
            
            // Create satellite entity with enhanced RF data
            const satEntity = this.viewer.entities.add({
                name: satellite.name,
                networkInfraType: 'satellite',
                position: position,
                satelliteType: satellite.type,
                operator: satellite.operator,
                orbit: satellite.orbit,
                altitude: satellite.altitude,
                frequencies: satellite.frequencies,
                coverage: satellite.coverage,
                status: satellite.status || 'Active',
                launchDate: satellite.launchDate,
                mission: satellite.mission,
                
                // RF Signal Processing Data
                rfSignalStrength: satellite.rfSignalStrength || Math.random() * 100,
                algorithmicRisk: satellite.algorithmicRisk || Math.random() * 0.8,
                dataFlowRate: satellite.dataFlowRate || Math.random() * 10000,
                
                billboard: {
                    image: this._getSatelliteIcon(satellite),
                    scale: this._getSatelliteScale(satellite),
                    color: this._getSatelliteColor(satellite),
                    heightReference: Cesium.HeightReference.NONE,
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    scaleByDistance: new Cesium.NearFarScalar(1000000, 3.0, 8000000, 1.5)
                },
                
                // Doppler-shifted RF Cone
                cylinder: ((satellite.algorithmicRisk || 0) > 0.6) ? 
                    this._createDopplerCone(satellite) : undefined,
                
                label: {
                    text: this._getSatelliteLabel(satellite),
                    font: '10px monospace',
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    outlineWidth: 2,
                    outlineColor: Cesium.Color.BLACK,
                    fillColor: this._getSatelliteLabelColor(satellite),
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    pixelOffset: new Cesium.Cartesian2(0, -25),
                    distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 5000000),
                    scaleByDistance: new Cesium.NearFarScalar(1000000, 1.5, 5000000, 0.8)
                }
            });
            
            // Orient cylinder towards Earth center
            if (satEntity.cylinder) {
                satEntity.orientation = new Cesium.CallbackProperty((time) => {
                    const center = Cesium.Cartesian3.ZERO;
                    const pos = satEntity.position.getValue(time);
                    const direction = Cesium.Cartesian3.normalize(
                        Cesium.Cartesian3.subtract(center, pos, new Cesium.Cartesian3()),
                        new Cesium.Cartesian3()
                    );
                    // Cylinder points along Z, need to rotate to point to Earth
                    return RF_SCYTHE._quaternionFromUnitVectors(Cesium.Cartesian3.UNIT_Z, direction);
                }, false);
            }
            
            this.entities.satellites.push(satEntity);
        }
        
        console.log(`Added ${this.entities.satellites.length} satellites with Doppler analysis`);
    } catch (error) {
        console.error('Error adding satellites:', error);
    }
};

/**
 * Create tower icon programmatically
 */
RF_SCYTHE.NetworkInfrastructure.prototype.createTowerIcon = function(color) {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const ctx = canvas.getContext('2d');
    
    // Draw tower shape
    ctx.fillStyle = color.toCssColorString();
    ctx.fillRect(14, 8, 4, 20);  // Tower mast
    ctx.fillRect(10, 6, 12, 4);  // Top crossbar
    ctx.fillRect(8, 12, 16, 2);  // Middle crossbar
    ctx.fillRect(6, 18, 20, 2);  // Bottom crossbar
    ctx.fillRect(12, 28, 8, 4);  // Base
    
    return canvas.toDataURL();
};

/**
 * Add cell towers to the visualization
 */
RF_SCYTHE.NetworkInfrastructure.prototype.addCellTowers = function(towers) {
    try {
        if (!towers || !Array.isArray(towers)) {
            console.warn('Invalid cell towers data');
            return;
        }
        
        for (const tower of towers) {
            if (!tower.name || !tower.position) {
                console.warn('Invalid tower data:', tower);
                continue;
            }
            
            const towerEntity = this.viewer.entities.add({
                name: tower.name,
                networkInfraType: 'tower',
                position: Cesium.Cartesian3.fromDegrees(
                    tower.position[0], 
                    tower.position[1], 
                    tower.position[2] || 50
                ),
                technology: tower.technology,
                operator: tower.operator,
                frequency: tower.frequency,
                coverage: tower.coverage,
                status: tower.status || 'Active',
                
                billboard: {
                    image: this.createTowerIcon(tower.status === 'Active' ? Cesium.Color.GREEN : Cesium.Color.GRAY),
                    scale: 0.8,
                    heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                },
                
                label: {
                    text: `${tower.name}\n${tower.technology || '5G'}`,
                    font: '10px monospace',
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    outlineWidth: 2,
                    outlineColor: Cesium.Color.BLACK,
                    fillColor: Cesium.Color.WHITE,
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    pixelOffset: new Cesium.Cartesian2(0, -30),
                    distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 1000000)
                }
            });
            
            this.entities.towers.push(towerEntity);
        }
        
        console.log(`Added ${this.entities.towers.length} cell towers`);
    } catch (error) {
        console.error('Error adding cell towers:', error);
    }
};

/**
 * Add undersea cables to the visualization
 */
RF_SCYTHE.NetworkInfrastructure.prototype.addUnderseaCables = function(cables) {
    try {
        if (!cables) {
            console.warn('Invalid undersea cables data');
            return;
        }
        
        // Handle different cable data formats
        let cableArray = [];
        if (Array.isArray(cables)) {
            cableArray = cables;
        } else if (typeof cables === 'object') {
            // Handle object with categories
            Object.values(cables).forEach(cableGroup => {
                if (Array.isArray(cableGroup)) {
                    cableArray = cableArray.concat(cableGroup);
                }
            });
        }
        
        for (const cable of cableArray) {
            if (!cable.name) {
                console.warn('Invalid cable data - missing name:', cable);
                continue;
            }
            
            // Handle different route field names (route, path, coordinates)
            let routeData = cable.route || cable.path || cable.coordinates;
            if (!routeData || !Array.isArray(routeData) || routeData.length < 2) {
                console.warn('Invalid cable data - missing or invalid route:', cable);
                continue;
            }
            
            try {
                // Convert route coordinates to Cesium positions
                const positions = routeData.map(coord => {
                    // Handle both [lat, lon] and [lon, lat] formats
                    let lat, lon;
                    if (Array.isArray(coord)) {
                        lat = coord[0];
                        lon = coord[1];
                    } else if (coord.latitude && coord.longitude) {
                        lat = coord.latitude;
                        lon = coord.longitude;
                    } else {
                        throw new Error('Invalid coordinate format');
                    }
                    return Cesium.Cartesian3.fromDegrees(lon, lat);
                });
                
                const cableEntity = this.viewer.entities.add({
                    name: cable.name,
                    networkInfraType: 'cable',
                    
                    polyline: {
                        positions: positions,
                        width: 4,
                        material: new Cesium.PolylineGlowMaterialProperty({
                            glowPower: 0.3,
                            color: Cesium.Color.CYAN.withAlpha(0.8)
                        }),
                        clampToGround: false,
                        followSurface: true
                    },
                    
                    // Store cable metadata
                    cableType: cable.type || 'undersea',
                    capacity: cable.capacity,
                    length: cable.length,
                    owners: cable.owners,
                    status: cable.status || 'Active',
                    yearBuilt: cable.yearBuilt
                });
                
                this.entities.cables.push(cableEntity);
                
            } catch (error) {
                console.warn('Error adding cable:', cable.name, error);
            }
        }
        
        console.log(`Added ${this.entities.cables.length} undersea cables`);
    } catch (error) {
        console.error('Error adding undersea cables:', error);
    }
};

/**
 * Create tower icon programmatically
 */
RF_SCYTHE.NetworkInfrastructure.prototype.createTowerIcon = function(color) {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const ctx = canvas.getContext('2d');
    
    // Draw tower shape
    ctx.fillStyle = color.toCssColorString();
    ctx.fillRect(14, 8, 4, 20);  // Tower mast
    ctx.fillRect(10, 6, 12, 4);  // Top crossbar
    ctx.fillRect(8, 12, 16, 2);  // Middle crossbar
    ctx.fillRect(6, 18, 20, 2);  // Bottom crossbar
    ctx.fillRect(12, 28, 8, 4);  // Base
    
    return canvas.toDataURL();
};

/**
 * Add fiber backbones (placeholder method)
 */
RF_SCYTHE.NetworkInfrastructure.prototype.addFiberBackbones = function(backbones) {
    try {
        console.log('Fiber backbones visualization - placeholder implementation');
        // Placeholder for future fiber backbone visualization
    } catch (error) {
        console.error('Error adding fiber backbones:', error);
    }
};

/**
 * Placeholder function for compatibility
 */
RF_SCYTHE.NetworkInfrastructure.prototype.enhancedRectangleNorthFix = function() {
    console.log('Enhanced rectangle north fix placeholder');
};

/**
 * Toggle visibility of entity types
 */
RF_SCYTHE.NetworkInfrastructure.prototype.toggleVisibility = function(type) {
    if (this.entities[type] && this.visible.hasOwnProperty(type)) {
        this.visible[type] = !this.visible[type];
        
        for (const entity of this.entities[type]) {
            entity.show = this.visible[type];
        }
        
        console.log(`${type} visibility: ${this.visible[type]}`);
    }
};

/**
 * Set satellites visibility
 * @param {boolean} visible - Whether satellites should be visible
 */
RF_SCYTHE.NetworkInfrastructure.prototype.setSatellitesVisible = function(visible) {
    this.visible.satellites = visible;
    if (this.entities.satellites) {
        this.entities.satellites.forEach(function(entity) {
            entity.show = visible;
        });
    }
    console.log('Satellites visibility:', visible);
};

/**
 * Set undersea cables visibility
 * @param {boolean} visible - Whether cables should be visible
 */
RF_SCYTHE.NetworkInfrastructure.prototype.setUnderseaCablesVisible = function(visible) {
    this.visible.cables = visible;
    if (this.entities.cables) {
        this.entities.cables.forEach(function(entity) {
            entity.show = visible;
        });
    }
    console.log('Undersea cables visibility:', visible);
};

/**
 * Set cell towers visibility
 * @param {boolean} visible - Whether towers should be visible
 */
RF_SCYTHE.NetworkInfrastructure.prototype.setCellTowersVisible = function(visible) {
    this.visible.towers = visible;
    if (this.entities.towers) {
        this.entities.towers.forEach(function(entity) {
            entity.show = visible;
        });
    }
    console.log('Cell towers visibility:', visible);
};

/**
 * Set fiber backbones visibility
 * @param {boolean} visible - Whether fiber backbones should be visible
 */
RF_SCYTHE.NetworkInfrastructure.prototype.setFiberBackbonesVisible = function(visible) {
    this.visible.servers = visible;  // Using 'servers' as fiber backbone storage
    if (this.entities.servers) {
        this.entities.servers.forEach(function(entity) {
            entity.show = visible;
        });
    }
    console.log('Fiber backbones visibility:', visible);
};

/**
 * Highlight entity on hover
 */
RF_SCYTHE.NetworkInfrastructure.prototype._highlightEntity = function(entity) {
    if (entity.billboard) {
        entity.billboard.scale = 1.5;
        entity.billboard.color = Cesium.Color.YELLOW;
    }
};

/**
 * Restore entity appearance
 */
RF_SCYTHE.NetworkInfrastructure.prototype._restoreEntityAppearance = function(entity) {
    if (entity.billboard) {
        entity.billboard.scale = entity.originalScale || 1.0;
        entity.billboard.color = entity.originalColor || Cesium.Color.WHITE;
    }
};

/**
 * Show tooltip
 */
RF_SCYTHE.NetworkInfrastructure.prototype._showTooltip = function(entity, position) {
    try {
        var name = 'Unknown';
        if (entity) {
            if (typeof entity.name === 'string') {
                name = entity.name;
            } else if (entity.name && entity.name.value) {
                name = entity.name.value;
            } else if (entity.id) {
                name = entity.id;
            } else if (entity._name) {
                name = entity._name;
            }
        }

        var content = '<div class="rfscythe-tooltip-title">' + (name || 'Unknown') + '</div>';

        var tooltip = document.getElementById('rfscythe-tooltip');
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = 'rfscythe-tooltip';
            tooltip.style.position = 'absolute';
            tooltip.style.pointerEvents = 'none';
            tooltip.style.background = 'rgba(0, 0, 0, 0.85)';
            tooltip.style.color = '#fff';
            tooltip.style.padding = '6px 8px';
            tooltip.style.borderRadius = '4px';
            tooltip.style.fontSize = '12px';
            tooltip.style.maxWidth = '360px';
            tooltip.style.boxSizing = 'border-box';
            tooltip.style.zIndex = 100000;
            tooltip.style.transition = 'opacity 0.12s ease';
            tooltip.style.opacity = '0';
            document.body.appendChild(tooltip);
        }

        tooltip.innerHTML = content;

        var x = 0, y = 0;
        if (position && typeof position.x === 'number' && typeof position.y === 'number') {
            x = position.x;
            y = position.y;
        } else if (position && typeof position.clientX === 'number' && typeof position.clientY === 'number') {
            x = position.clientX;
            y = position.clientY;
        }

        // Offset a little so tooltip does not sit under the cursor
        tooltip.style.left = (x + 12) + 'px';
        tooltip.style.top = (y + 12) + 'px';
        tooltip.style.opacity = '1';
    } catch (error) {
        console.error('Error showing tooltip:', error);
    }
};

/**
 * Hide tooltip
 */
RF_SCYTHE.NetworkInfrastructure.prototype._hideTooltip = function() {
    try {
        var tooltip = document.getElementById('rfscythe-tooltip');
        if (tooltip) {
            tooltip.style.opacity = '0';
            window.setTimeout(function() {
                if (tooltip && tooltip.parentNode) {
                    tooltip.parentNode.removeChild(tooltip);
                }
            }, 160);
        }
    } catch (error) {
        console.error('Error hiding tooltip:', error);
    }
};

/**
 * Show detailed info
 */
RF_SCYTHE.NetworkInfrastructure.prototype._showDetailedInfo = function(entity) {
    // Implementation would show detailed information panel
    console.log('Showing detailed info for:', entity.name);
};

/**
 * Highlight a path that a violator has taken across different infrastructure
 * elements.  This method is provided as a stub in case the full
 * implementation is not available.  It simply logs the input for now.
 *
 * @param {Object|Array} pathData An object or array describing the sequence of
 * coordinates or infrastructure node identifiers to highlight.
 */
RF_SCYTHE.NetworkInfrastructure.prototype.highlightViolatorPath = function(pathData) {
    console.warn('[RF_SCYTHE] highlightViolatorPath fallback invoked', pathData);
    // A real implementation might build a polyline through the specified
    // infrastructure nodes and apply a blinking material to draw attention.
};