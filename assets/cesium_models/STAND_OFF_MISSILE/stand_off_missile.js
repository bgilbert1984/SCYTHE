/**
 * Stand-Off Missile Model Integration for RF SCYTHE
 *
 * This script provides functions to load and animate the Stand-Off Missile
 * model in the RF SCYTHE visualization system.
 */

window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * Load a Stand-Off Missile model at the specified position
 * @param {Cesium.Viewer} viewer - The Cesium viewer
 * @param {Cesium.Cartesian3} position - Position for the missile
 * @param {Object} options - Configuration options
 * @returns {Cesium.Entity} The created missile entity
 */
RF_SCYTHE.loadStandOffMissile = function(viewer, position, options = {}) {
    const defaultOptions = {
        scale: 1.0,
        heading: 0,
        pitch: 0,
        roll: 0,
        color: Cesium.Color.WHITE,
        name: "Stand-Off Missile",
        showPath: false,
        pathColor: Cesium.Color.RED,
        animate: false,
        speed: 200 // meters per second
    };

    // Merge options
    const settings = {...defaultOptions, ...options};

    try {
        // Create the missile entity
        const entity = viewer.entities.add({
            name: settings.name,
            position: position,
            model: {
                uri: 'assets/cesium_models/STAND_OFF_MISSILE.glb',
                minimumPixelSize: 128,
                maximumScale: 20000,
                scale: settings.scale,
                color: settings.color,
                colorBlendMode: Cesium.ColorBlendMode.HIGHLIGHT
            },
            orientation: Cesium.Transforms.headingPitchRollQuaternion(
                position,
                new Cesium.HeadingPitchRoll(
                    Cesium.Math.toRadians(settings.heading),
                    Cesium.Math.toRadians(settings.pitch),
                    Cesium.Math.toRadians(settings.roll)
                )
            )
        });

        // Add path if requested
        if (settings.showPath) {
            entity.path = {
                resolution: 1,
                material: new Cesium.PolylineGlowMaterialProperty({
                    glowPower: 0.1,
                    color: settings.pathColor
                }),
                width: 10
            };
        }

        // Add animation if requested
        if (settings.animate) {
            const startTime = Cesium.JulianDate.now();
            const startPosition = position.clone();

            // Set up position property
            entity.position = new Cesium.CallbackProperty((time) => {
                const elapsedSeconds = Cesium.JulianDate.secondsDifference(time, startTime);

                // Calculate new position based on orientation and elapsed time
                const headingRadians = Cesium.Math.toRadians(settings.heading);
                const pitchRadians = Cesium.Math.toRadians(settings.pitch);

                // Simple movement along the missile's heading
                const distance = elapsedSeconds * settings.speed;

                // Get the direction vector based on heading and pitch
                const direction = new Cesium.Cartesian3(
                    Math.sin(headingRadians) * Math.cos(pitchRadians),
                    Math.cos(headingRadians) * Math.cos(pitchRadians),
                    Math.sin(pitchRadians)
                );

                // Normalize and scale by distance
                Cesium.Cartesian3.normalize(direction, direction);
                Cesium.Cartesian3.multiplyByScalar(direction, distance, direction);

                // Add to start position
                const newPosition = Cesium.Cartesian3.add(startPosition, direction, new Cesium.Cartesian3());
                return newPosition;
            }, false);
        }

        return entity;
    } catch (error) {
        console.error("Error loading Stand-Off Missile:", error);
        return null;
    }
};

/**
 * Launch a missile from one position to another
 * @param {Cesium.Viewer} viewer - The Cesium viewer
 * @param {Cesium.Cartesian3} startPosition - Launch position
 * @param {Cesium.Cartesian3} endPosition - Target position
 * @param {Object} options - Configuration options
 * @returns {Cesium.Entity} The created missile entity
 */
RF_SCYTHE.launchMissile = function(viewer, startPosition, endPosition, options = {}) {
    const defaultOptions = {
        scale: 0.8,
        color: Cesium.Color.WHITE,
        name: "Stand-Off Missile",
        showPath: true,
        pathColor: Cesium.Color.RED.withAlpha(0.7),
        speed: 300, // meters per second
        arcHeight: 5000, // meters
        onComplete: null
    };

    // Merge options
    const settings = {...defaultOptions, ...options};

    try {
        // Calculate direction from start to end
        const direction = Cesium.Cartesian3.subtract(
            endPosition,
            startPosition,
            new Cesium.Cartesian3()
        );

        const distance = Cesium.Cartesian3.magnitude(direction);
        Cesium.Cartesian3.normalize(direction, direction);

        // Calculate flight time
        const flightTimeSeconds = distance / settings.speed;

        // Define the start and end times
        const start = Cesium.JulianDate.now();
        const end = Cesium.JulianDate.addSeconds(start, flightTimeSeconds, new Cesium.JulianDate());

        // Make the viewer timeline show this period
        viewer.clock.startTime = start.clone();
        viewer.clock.stopTime = end.clone();
        viewer.clock.currentTime = start.clone();
        viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP;
        viewer.clock.multiplier = 1.0;

        // Calculate heading/pitch from direction
        const heading = Math.atan2(direction.y, direction.x);
        const pitch = Math.asin(direction.z / Cesium.Cartesian3.magnitude(direction));

        // Add the entity
        const entity = viewer.entities.add({
            name: settings.name,
            availability: new Cesium.TimeIntervalCollection([
                new Cesium.TimeInterval({
                    start: start,
                    stop: end
                })
            ]),
            model: {
                uri: 'assets/cesium_models/STAND_OFF_MISSILE.glb',
                minimumPixelSize: 128,
                maximumScale: 20000,
                scale: settings.scale,
                color: settings.color,
                colorBlendMode: Cesium.ColorBlendMode.HIGHLIGHT
            },
            // Position based on time
            position: new Cesium.SampledPositionProperty(),
            // Orientation based on velocity
            orientation: new Cesium.VelocityOrientationProperty(new Cesium.SampledPositionProperty())
        });

        // Add path if requested
        if (settings.showPath) {
            entity.path = {
                resolution: 1,
                material: new Cesium.PolylineGlowMaterialProperty({
                    glowPower: 0.3,
                    color: settings.pathColor
                }),
                width: 10,
                leadTime: 0,
                trailTime: flightTimeSeconds
            };
        }

        // Compute trajectory with arc
        const property = entity.position;

        // Function to add positions along a ballistic arc
        for (let i = 0; i <= 100; i++) {
            const t = i / 100.0;
            const time = Cesium.JulianDate.addSeconds(
                start,
                t * flightTimeSeconds,
                new Cesium.JulianDate()
            );

            // Compute position along a ballistic arc
            const pos = new Cesium.Cartesian3();
            Cesium.Cartesian3.lerp(
                startPosition,
                endPosition,
                t,
                pos
            );

            // Add arc height (parabola)
            const arcFactor = t * (1.0 - t);
            const upVector = Cesium.Cartesian3.normalize(pos, new Cesium.Cartesian3());
            const arcVector = Cesium.Cartesian3.multiplyByScalar(
                upVector,
                arcFactor * settings.arcHeight,
                new Cesium.Cartesian3()
            );

            Cesium.Cartesian3.add(pos, arcVector, pos);

            // Add sample
            property.addSample(time, pos);
        }

        // Set derivative with velocity orientation
        entity.orientation.velocityReference = entity.position;

        // Event to handle completion
        if (settings.onComplete) {
            // Register event to detect completion
            viewer.clock.onTick.addEventListener(function onTick(clock) {
                if (Cesium.JulianDate.greaterThanOrEquals(clock.currentTime, end)) {
                    viewer.clock.onTick.removeEventListener(onTick);
                    settings.onComplete(entity);
                }
            });
        }

        return entity;
    } catch (error) {
        console.error("Error launching missile:", error);
        return null;
    }
};

/**
 * Launch multiple missiles at a target
 * @param {Cesium.Viewer} viewer - The Cesium viewer
 * @param {Cesium.Cartesian3} targetPosition - The target position
 * @param {Array} launchPositions - Array of launch positions
 * @param {Object} options - Configuration options
 * @returns {Array} Array of missile entities
 */
RF_SCYTHE.launchMissileBarrage = function(viewer, targetPosition, launchPositions, options = {}) {
    const defaultOptions = {
        sequential: false,
        interval: 1000, // ms between launches if sequential
        color: null, // auto-generate colors if null
        arcHeightVariation: 2000, // variation in arc height
        onComplete: null
    };

    // Merge options
    const settings = {...defaultOptions, ...options};
    const missiles = [];

    try {
        const launchCount = launchPositions.length;

        for (let i = 0; i < launchCount; i++) {
            const launchFn = () => {
                // Generate a color if not specified
                const color = settings.color ||
                    Cesium.Color.fromHsl((i / launchCount) * 360, 1.0, 0.5);

                // Vary arc height
                const arcHeight = settings.arcHeight ||
                    5000 + Math.random() * settings.arcHeightVariation;

                const missile = RF_SCYTHE.launchMissile(
                    viewer,
                    launchPositions[i],
                    targetPosition,
                    {
                        scale: 0.7,
                        color: color,
                        pathColor: color.withAlpha(0.7),
                        arcHeight: arcHeight,
                        speed: 300 + (Math.random() * 50),
                        onComplete: i === launchCount - 1 ? settings.onComplete : null
                    }
                );

                missiles.push(missile);
            };

            if (settings.sequential) {
                setTimeout(launchFn, i * settings.interval);
            } else {
                launchFn();
            }
        }

        return missiles;
    } catch (error) {
        console.error("Error launching missile barrage:", error);
        return missiles;
    }
};

// Log that the script has loaded
console.log("Stand-Off Missile integration loaded for RF SCYTHE");
