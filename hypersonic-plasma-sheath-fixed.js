/**
 * Hypersonic Plasma Sheath Simulation for RF SCYTHE
 *
 * This script provides simulations for hypersonic missile plasma sheaths
 * and related defense mechanisms like plasma sheath disruption.
 */

window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * Hypersonic Missile Physics Constants
 */
RF_SCYTHE.HYPERSONIC = {
    // Plasma sheath formation threshold (Mach 5+)
    PLASMA_THRESHOLD_SPEED: 1700, // m/s (~Mach 5)

    // Plasma properties
    PLASMA_MAX_TEMPERATURE: 11000, // Kelvin
    PLASMA_DENSITY_MAX: 10e15, // Particles per cubic meter at peak

    // Missile aerodynamics
    DRAG_COEFFICIENT_NORMAL: 0.05,
    DRAG_COEFFICIENT_DISRUPTED: 0.8,

    // RF properties
    RF_ATTENUATION_MIN: 0.3, // Minimum RF attenuation factor
    RF_ATTENUATION_MAX: 0.98, // Maximum RF attenuation at peak plasma

    // Disruption effect parameters
    DISRUPTION_RECOVERY_TIME: 3.0, // seconds to recover stability
    DISRUPTION_WOBBLE_FREQUENCY: 5.0, // Hz - oscillation frequency
    DISRUPTION_WOBBLE_AMPLITUDE: 20.0, // degrees - max deviation angle
    DISRUPTION_COURSE_DEVIATION: 0.2, // Maximum course deviation as fraction

    // Material ablation
    ABLATION_RATE: 0.02, // mm/s material removal rate

    // Visual effects
    PLASMA_COLORS: [
        Cesium.Color.fromCssColorString('#ff5e00'), // Orange core
        Cesium.Color.fromCssColorString('#ff9d00'), // Yellow-orange
        Cesium.Color.fromCssColorString('#ffcb66'), // Light yellow
        Cesium.Color.fromCssColorString('#ffebcc').withAlpha(0.8) // Faint outer glow
    ]
};

/**
 * Helper function to create a proper color material property
 * This ensures we return a valid MaterialProperty for Cesium
 * @param {Function|Cesium.Color} colorOrFunction - Color or function returning color
 * @returns {Cesium.MaterialProperty} A valid material property
 */
RF_SCYTHE.createColorMaterialProperty = function(colorOrFunction) {
    if (typeof colorOrFunction === 'function') {
        // For callback functions, create a CallbackProperty that returns a ColorMaterialProperty
        return new Cesium.CallbackProperty(function(time) {
            const color = colorOrFunction(time);
            return new Cesium.ColorMaterialProperty(color);
        }, false);
    } else {
        // For direct color values
        return new Cesium.ColorMaterialProperty(colorOrFunction);
    }
};

/**
 * Calculate hypersonic plasma sheath properties based on missile speed
 * @param {Number} speed - Missile speed in m/s
 * @returns {Object} Plasma sheath properties
 */
RF_SCYTHE.calculatePlasmaProperties = function(speed) {
    // No plasma below threshold
    if (speed < RF_SCYTHE.HYPERSONIC.PLASMA_THRESHOLD_SPEED) {
        return {
            exists: false,
            temperature: 0,
            density: 0,
            thickness: 0,
            rfAttenuation: 0,
            color: Cesium.Color.WHITE.withAlpha(0)
        };
    }

    // Calculate plasma intensity (0-1) based on speed
    const mach = speed / 343; // rough mach number (343 m/s is speed of sound)
    const machRelative = Math.max(0, Math.min(1, (mach - 5) / 15)); // 0 at Mach 5, 1 at Mach 20

    // Calculate plasma properties
    const temperature = 2000 + (RF_SCYTHE.HYPERSONIC.PLASMA_MAX_TEMPERATURE - 2000) * machRelative;
    const density = RF_SCYTHE.HYPERSONIC.PLASMA_DENSITY_MAX * machRelative;
    const thickness = 0.2 + 0.8 * machRelative; // 0.2m to 1.0m

    // RF attenuation increases with plasma density
    const rfAttenuation = RF_SCYTHE.HYPERSONIC.RF_ATTENUATION_MIN +
        (RF_SCYTHE.HYPERSONIC.RF_ATTENUATION_MAX - RF_SCYTHE.HYPERSONIC.RF_ATTENUATION_MIN) *
        Math.pow(machRelative, 1.5);

    // Calculate color based on temperature
    let color;
    if (temperature < 6000) {
        // Blend between orange and yellow-orange
        const t = (temperature - 2000) / 4000;
        color = Cesium.Color.lerp(
            RF_SCYTHE.HYPERSONIC.PLASMA_COLORS[0],
            RF_SCYTHE.HYPERSONIC.PLASMA_COLORS[1],
            t,
            new Cesium.Color()
        );
    } else {
        // Blend between yellow-orange and light yellow
        const t = (temperature - 6000) / 5000;
        color = Cesium.Color.lerp(
            RF_SCYTHE.HYPERSONIC.PLASMA_COLORS[1],
            RF_SCYTHE.HYPERSONIC.PLASMA_COLORS[2],
            t,
            new Cesium.Color()
        );
    }

    return {
        exists: true,
        temperature: temperature,
        density: density,
        thickness: thickness,
        rfAttenuation: rfAttenuation,
        color: color,
        machNumber: mach
    };
};

/**
 * Add a plasma sheath to a hypersonic missile
 * @param {Cesium.Viewer} viewer - The Cesium viewer
 * @param {Cesium.Entity} missileEntity - The missile entity
 * @param {Object} options - Configuration options
 * @returns {Object} Plasma sheath visualization objects
 */
RF_SCYTHE.addPlasmaSheath = function(viewer, missileEntity, options = {}) {
    const defaultOptions = {
        minSpeed: RF_SCYTHE.HYPERSONIC.PLASMA_THRESHOLD_SPEED,
        useGlow: true,
        useParticles: true,
        scale: 1.0
    };

    // Merge options
    const settings = {...defaultOptions, ...options};

    // Create a plasma sheath entity
    const plasmaEntity = viewer.entities.add({
        name: 'Plasma Sheath',
        parent: missileEntity,
        position: new Cesium.ConstantPositionProperty(Cesium.Cartesian3.ZERO),
        ellipsoid: {
            radii: new Cesium.CallbackProperty(function(time) {
                // Get missile position to calculate speed
                const position = missileEntity.position.getValue(time);
                if (!position) return new Cesium.Cartesian3(1, 1, 1);

                // Get speed from velocity if available
                let speed = 0;
                if (missileEntity.velocity) {
                    const velocity = missileEntity.velocity.getValue(time);
                    if (velocity) {
                        speed = Cesium.Cartesian3.magnitude(velocity);
                    }
                } else if (time && missileEntity.position) {
                    // Estimate velocity from position change
                    const prevTime = Cesium.JulianDate.addSeconds(time, -0.1, new Cesium.JulianDate());
                    const prevPos = missileEntity.position.getValue(prevTime);
                    if (prevPos) {
                        const delta = Cesium.Cartesian3.subtract(position, prevPos, new Cesium.Cartesian3());
                        speed = Cesium.Cartesian3.magnitude(delta) * 10; // 0.1s → multiply by 10 for m/s
                    }
                }

                // Calculate plasma properties
                const plasma = RF_SCYTHE.calculatePlasmaProperties(speed);

                // No visible plasma below threshold
                if (!plasma.exists) {
                    return new Cesium.Cartesian3(0.1, 0.1, 0.1);
                }

                // Get model dimensions and orientation
                const modelMatrix = missileEntity.computeModelMatrix(time);

                // Base plasma sheath size on model scale and plasma thickness
                const baseSize = 2.0 * settings.scale; // Base size relative to missile
                const length = baseSize * 4.0; // Longer in missile axis
                const width = baseSize * plasma.thickness * (1 + plasma.machNumber * 0.02);
                const height = width;

                return new Cesium.Cartesian3(width, height, length);
            }, false),
            // Fix: Use proper material property instead of direct color callback
            material: RF_SCYTHE.createColorMaterialProperty(function(time) {
                // Get missile position to calculate speed
                const position = missileEntity.position.getValue(time);
                if (!position) return Cesium.Color.WHITE.withAlpha(0);

                // Get speed from velocity if available
                let speed = 0;
                if (missileEntity.velocity) {
                    const velocity = missileEntity.velocity.getValue(time);
                    if (velocity) {
                        speed = Cesium.Cartesian3.magnitude(velocity);
                    }
                } else if (time && missileEntity.position) {
                    // Estimate velocity from position change
                    const prevTime = Cesium.JulianDate.addSeconds(time, -0.1, new Cesium.JulianDate());
                    const prevPos = missileEntity.position.getValue(prevTime);
                    if (prevPos) {
                        const delta = Cesium.Cartesian3.subtract(position, prevPos, new Cesium.Cartesian3());
                        speed = Cesium.Cartesian3.magnitude(delta) * 10; // 0.1s → multiply by 10 for m/s
                    }
                }

                // Calculate plasma properties
                const plasma = RF_SCYTHE.calculatePlasmaProperties(speed);

                // No visible plasma below threshold
                if (!plasma.exists) {
                    return Cesium.Color.WHITE.withAlpha(0);
                }

                return new Cesium.Color(
                    plasma.color.red,
                    plasma.color.green,
                    plasma.color.blue,
                    Math.min(0.7, plasma.density / RF_SCYTHE.HYPERSONIC.PLASMA_DENSITY_MAX)
                );
            }),
            heightReference: Cesium.HeightReference.NONE,
            outline: false,
            slicePartitions: 24,
            stackPartitions: 24
        }
    });

    // Add glowing halo effect if requested
    let glowEntity = null;
    if (settings.useGlow) {
        glowEntity = viewer.entities.add({
            name: 'Plasma Glow',
            parent: missileEntity,
            position: new Cesium.ConstantPositionProperty(Cesium.Cartesian3.ZERO),
            ellipsoid: {
                radii: new Cesium.CallbackProperty(function(time) {
                    const position = missileEntity.position.getValue(time);
                    if (!position) return new Cesium.Cartesian3(1, 1, 1);

                    // Estimate speed similar to above
                    let speed = 0;
                    if (missileEntity.velocity) {
                        const velocity = missileEntity.velocity.getValue(time);
                        if (velocity) {
                            speed = Cesium.Cartesian3.magnitude(velocity);
                        }
                    } else if (time && missileEntity.position) {
                        const prevTime = Cesium.JulianDate.addSeconds(time, -0.1, new Cesium.JulianDate());
                        const prevPos = missileEntity.position.getValue(prevTime);
                        if (prevPos) {
                            const delta = Cesium.Cartesian3.subtract(position, prevPos, new Cesium.Cartesian3());
                            speed = Cesium.Cartesian3.magnitude(delta) * 10;
                        }
                    }

                    // Calculate plasma properties
                    const plasma = RF_SCYTHE.calculatePlasmaProperties(speed);

                    // No visible plasma below threshold
                    if (!plasma.exists) {
                        return new Cesium.Cartesian3(0.1, 0.1, 0.1);
                    }

                    // Glow is bigger than the main plasma sheath
                    const baseSize = 3.0 * settings.scale;
                    const length = baseSize * 5.0;
                    const width = baseSize * plasma.thickness * 1.5 * (1 + plasma.machNumber * 0.05);
                    const height = width;

                    return new Cesium.Cartesian3(width, height, length);
                }, false),
                // Fix: Use proper material property instead of direct color callback
                material: RF_SCYTHE.createColorMaterialProperty(function(time) {
                    const position = missileEntity.position.getValue(time);
                    if (!position) return Cesium.Color.WHITE.withAlpha(0);

                    // Calculate speed similar to above
                    let speed = 0;
                    if (missileEntity.velocity) {
                        const velocity = missileEntity.velocity.getValue(time);
                        if (velocity) {
                            speed = Cesium.Cartesian3.magnitude(velocity);
                        }
                    } else if (time && missileEntity.position) {
                        const prevTime = Cesium.JulianDate.addSeconds(time, -0.1, new Cesium.JulianDate());
                        const prevPos = missileEntity.position.getValue(prevTime);
                        if (prevPos) {
                            const delta = Cesium.Cartesian3.subtract(position, prevPos, new Cesium.Cartesian3());
                            speed = Cesium.Cartesian3.magnitude(delta) * 10;
                        }
                    }

                    // Calculate plasma properties
                    const plasma = RF_SCYTHE.calculatePlasmaProperties(speed);

                    // No visible plasma below threshold
                    if (!plasma.exists) {
                        return Cesium.Color.WHITE.withAlpha(0);
                    }

                    // Outer glow is more transparent
                    return new Cesium.Color(
                        RF_SCYTHE.HYPERSONIC.PLASMA_COLORS[3].red,
                        RF_SCYTHE.HYPERSONIC.PLASMA_COLORS[3].green,
                        RF_SCYTHE.HYPERSONIC.PLASMA_COLORS[3].blue,
                        Math.min(0.4, 0.1 + 0.3 * plasma.density / RF_SCYTHE.HYPERSONIC.PLASMA_DENSITY_MAX)
                    );
                }),
                heightReference: Cesium.HeightReference.NONE,
                outline: false,
                slicePartitions: 24,
                stackPartitions: 24
            }
        });
    }

    return {
        plasmaEntity: plasmaEntity,
        glowEntity: glowEntity
    };
};

/**
 * Apply disruption to a missile's plasma sheath
 * @param {Cesium.Viewer} viewer - The Cesium viewer
 * @param {Cesium.Entity} missileEntity - The missile entity
 * @param {Object} plasmaObjects - Plasma sheath objects from addPlasmaSheath
 * @param {Object} options - Configuration options
 * @returns {Object} Disruption effects controller
 */
RF_SCYTHE.disruptPlasmaSheath = function(viewer, missileEntity, plasmaObjects, options = {}) {
    const defaultOptions = {
        duration: 5.0, // seconds
        intensity: 1.0, // 0-1 scale
        disruptPosition: null // Position of disruption (defaults to missile's current position)
    };

    // Merge options
    const settings = {...defaultOptions, ...options};

    // Get current time
    const startTime = Cesium.JulianDate.now();

    // Get disruption position (defaults to missile's current position)
    const disruptPosition = settings.disruptPosition ||
        missileEntity.position.getValue(startTime);

    // Create disruption visual effect
    const disruptEntity = viewer.entities.add({
        position: disruptPosition,
        ellipsoid: {
            radii: new Cesium.CallbackProperty(function(time) {
                const elapsedTime = Cesium.JulianDate.secondsDifference(time, startTime);

                // Initial expansion followed by fade
                const size = Math.min(500, elapsedTime * 200);

                // Remove entity after duration
                if (elapsedTime > settings.duration) {
                    viewer.entities.remove(disruptEntity);
                    return new Cesium.Cartesian3(1, 1, 1);
                }

                return new Cesium.Cartesian3(size, size, size);
            }, false),
            // Fix: Use proper material property
            material: RF_SCYTHE.createColorMaterialProperty(function(time) {
                const elapsedTime = Cesium.JulianDate.secondsDifference(time, startTime);

                // Fade out over time
                const alpha = Math.max(0, 0.7 - elapsedTime / settings.duration * 0.7);

                return Cesium.Color.AQUA.withAlpha(alpha);
            }),
            outline: true,
            outlineColor: new Cesium.CallbackProperty(function(time) {
                const elapsedTime = Cesium.JulianDate.secondsDifference(time, startTime);

                // Fade out over time
                const alpha = Math.max(0, 0.9 - elapsedTime / settings.duration * 0.9);

                return Cesium.Color.CYAN.withAlpha(alpha);
            }, false)
        }
    });

    // Store original orientation function
    const originalOrientation = missileEntity.orientation;

    // Apply wobble effect to the missile
    missileEntity.orientation = new Cesium.CallbackProperty(function(time) {
        const elapsedTime = Cesium.JulianDate.secondsDifference(time, startTime);

        // Get base orientation
        let baseOrientation;
        if (typeof originalOrientation === 'function') {
            baseOrientation = originalOrientation(time);
        } else if (originalOrientation && originalOrientation.getValue) {
            baseOrientation = originalOrientation.getValue(time);
        }

        // After effect duration, restore original orientation
        if (elapsedTime > settings.duration) {
            missileEntity.orientation = originalOrientation;
            return baseOrientation;
        }

        // Apply wobble based on sine waves at different frequencies
        const wobbleIntensity = settings.intensity *
            Math.max(0, 1.0 - elapsedTime / settings.duration);

        // Multiple oscillations for more chaotic effect
        const pitchWobble = wobbleIntensity *
            Math.sin(elapsedTime * RF_SCYTHE.HYPERSONIC.DISRUPTION_WOBBLE_FREQUENCY) *
            RF_SCYTHE.HYPERSONIC.DISRUPTION_WOBBLE_AMPLITUDE;

        const yawWobble = wobbleIntensity *
            Math.sin(elapsedTime * RF_SCYTHE.HYPERSONIC.DISRUPTION_WOBBLE_FREQUENCY * 1.3) *
            RF_SCYTHE.HYPERSONIC.DISRUPTION_WOBBLE_AMPLITUDE;

        const rollWobble = wobbleIntensity *
            Math.sin(elapsedTime * RF_SCYTHE.HYPERSONIC.DISRUPTION_WOBBLE_FREQUENCY * 0.7) *
            RF_SCYTHE.HYPERSONIC.DISRUPTION_WOBBLE_AMPLITUDE * 2;

        // Convert to radians
        const pitchOffset = Cesium.Math.toRadians(pitchWobble);
        const yawOffset = Cesium.Math.toRadians(yawWobble);
        const rollOffset = Cesium.Math.toRadians(rollWobble);

        // Create a quaternion from the wobble angles
        const wobbleQuaternion = Cesium.Quaternion.fromHeadingPitchRoll(
            new Cesium.HeadingPitchRoll(yawOffset, pitchOffset, rollOffset)
        );

        // Combine with base orientation
        return Cesium.Quaternion.multiply(baseOrientation, wobbleQuaternion, new Cesium.Quaternion());
    }, false);

    // Also modify the path if it exists
    if (missileEntity.path) {
        // Store the original path's lead and trail times
        const originalLeadTime = missileEntity.path.leadTime ?
            missileEntity.path.leadTime.getValue() : 0;
        const originalTrailTime = missileEntity.path.trailTime ?
            missileEntity.path.trailTime.getValue() : 60;

        // Make the trail show the wobbling path
        missileEntity.path.leadTime = 0;
        missileEntity.path.trailTime = settings.duration;

        // Restore original after duration
        setTimeout(function() {
            if (missileEntity.path) {
                missileEntity.path.leadTime = originalLeadTime;
                missileEntity.path.trailTime = originalTrailTime;
            }
        }, settings.duration * 1000);
    }

    // Return controller with ability to cancel disruption
    return {
        cancel: function() {
            // Restore original orientation
            missileEntity.orientation = originalOrientation;

            // Remove disruption entity
            viewer.entities.remove(disruptEntity);
        }
    };
};

// Error handling during initialization
try {
    // Log that the script has loaded
    console.log("Hypersonic Plasma Sheath simulation loaded for RF SCYTHE");
} catch (e) {
    console.error("Error initializing Hypersonic Plasma Sheath simulation:", e);
}
