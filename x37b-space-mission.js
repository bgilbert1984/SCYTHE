/**
 * X-37B Space Plane Mission Simulation
 *
 * This module simulates the X-37B Space Plane collecting old space objects
 * and delivering them to the ISS for use as raw materials for expansion.
 *
 * Part of the RF SCYTHE project.
 */

window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * X-37B Mission Simulation
 */
RF_SCYTHE.X37BMission = (function() {
    // Private variables
    let viewer = null;
    let x37bEntity = null;
    let issEntity = null;
    let spaceObjectEntities = [];
    let collectedObjects = [];
    let orbitPath = null;
    let missionActive = false;
    let missionStatus = 'idle';
    let missionClock = 0;
    let clockInterval = null;
    let currentTarget = null;
    let deliveryInProgress = false;

    // Collection of known old space objects with real data
    // These are some of the oldest objects still in orbit
    const spaceObjects = [
        {
            id: 'vanguard1',
            name: 'Vanguard 1',
            type: 'Satellite',
            launchDate: '1958-03-17',
            description: 'The oldest satellite still in orbit, launched in 1958',
            mass: 1.47, // kg
            semiMajorAxis: 8620, // km
            eccentricity: 0.1903,
            inclination: 34.25, // degrees
            color: Cesium.Color.SILVER,
            size: 0.16 // meters
        },
        {
            id: 'vanguard2',
            name: 'Vanguard 2',
            type: 'Satellite',
            launchDate: '1959-02-17',
            description: 'Second in the Vanguard series, launched in 1959',
            mass: 9.8, // kg
            semiMajorAxis: 8210, // km
            eccentricity: 0.1645,
            inclination: 32.88, // degrees
            color: Cesium.Color.SILVER,
            size: 0.5 // meters
        },
        {
            id: 'explorer7',
            name: 'Explorer 7',
            type: 'Satellite',
            launchDate: '1959-10-13',
            description: 'Studied solar radiation and micrometeorites',
            mass: 41.5, // kg
            semiMajorAxis: 7430, // km
            eccentricity: 0.014,
            inclination: 50.3, // degrees
            color: Cesium.Color.DARKGRAY,
            size: 0.76 // meters
        },
        {
            id: 'transit4a',
            name: 'Transit 4A',
            type: 'Satellite',
            launchDate: '1961-06-29',
            description: 'Early navigation satellite',
            mass: 79, // kg
            semiMajorAxis: 7800, // km
            eccentricity: 0.0075,
            inclination: 66.8, // degrees
            color: Cesium.Color.YELLOW,
            size: 0.9 // meters
        },
        {
            id: 'atlas_rocket_body',
            name: 'Atlas Centaur 2 Rocket Body',
            type: 'Rocket Body',
            launchDate: '1963-11-27',
            description: 'Rocket body from early Atlas Centaur launch',
            mass: 1900, // kg
            semiMajorAxis: 7700, // km
            eccentricity: 0.0433,
            inclination: 30.4, // degrees
            color: Cesium.Color.DARKGRAY,
            size: 3.05 // meters
        },
        {
            id: 'thor_agena_rocket_body',
            name: 'Thor Agena Rocket Body',
            type: 'Rocket Body',
            launchDate: '1965-05-06',
            description: 'Rocket body from Thor Agena launch',
            mass: 1400, // kg
            semiMajorAxis: 7900, // km
            eccentricity: 0.0023,
            inclination: 70.1, // degrees
            color: Cesium.Color.DARKGRAY,
            size: 2.44 // meters
        },
        {
            id: 'solwind',
            name: 'Solwind P78-1 Debris',
            type: 'Debris',
            launchDate: '1979-02-24',
            description: 'Debris from ASAT test against Solwind P78-1 satellite',
            mass: 15, // kg
            semiMajorAxis: 8100, // km
            eccentricity: 0.0098,
            inclination: 96.5, // degrees
            color: Cesium.Color.RED,
            size: 0.4 // meters
        },
        {
            id: 'cosmos_2251_debris',
            name: 'Cosmos 2251 Debris',
            type: 'Debris',
            launchDate: '1993-06-16',
            description: 'Debris from Cosmos 2251 collision with Iridium 33',
            mass: 5, // kg
            semiMajorAxis: 7500, // km
            eccentricity: 0.0243,
            inclination: 74.2, // degrees
            color: Cesium.Color.RED,
            size: 0.3 // meters
        }
    ];

    // Initialize the ISS orbit
    const issOrbit = {
        semiMajorAxis: 6771, // km
        eccentricity: 0.0004,
        inclination: 51.64, // degrees
        rightAscension: 190, // degrees
        argumentOfPeriapsis: 90 // degrees
    };

    // Helper function to convert orbital elements to Cartesian position
    function computeOrbitalPosition(orbit, timeOffset = 0) {
        // Mean motion (rad/s)
        const mu = 3.986004418e14; // Earth's gravitational parameter (m^3/s^2)
        const semiMajorAxisMeters = orbit.semiMajorAxis * 1000;
        const meanMotion = Math.sqrt(mu / Math.pow(semiMajorAxisMeters, 3));

        // Mean anomaly
        const meanAnomaly = (meanMotion * timeOffset) % (2 * Math.PI);

        // Eccentric anomaly (iterative solution)
        let eccAnomaly = meanAnomaly;
        for (let i = 0; i < 10; i++) {
            eccAnomaly = meanAnomaly + orbit.eccentricity * Math.sin(eccAnomaly);
        }

        // True anomaly
        const trueAnomaly = 2 * Math.atan2(
            Math.sqrt(1 + orbit.eccentricity) * Math.sin(eccAnomaly / 2),
            Math.sqrt(1 - orbit.eccentricity) * Math.cos(eccAnomaly / 2)
        );

        // Distance from focus
        const distance = semiMajorAxisMeters * (1 - orbit.eccentricity * Math.cos(eccAnomaly));

        // Position in orbital plane
        const x = distance * Math.cos(trueAnomaly);
        const y = distance * Math.sin(trueAnomaly);

        // Convert to Cartesian coordinates using rotation matrices
        // This is a simplified calculation and would need to be more precise for a real application
        const incRad = Cesium.Math.toRadians(orbit.inclination);
        const raanRad = Cesium.Math.toRadians(orbit.rightAscension || 0);
        const aopRad = Cesium.Math.toRadians(orbit.argumentOfPeriapsis || 0);

        // Apply rotations (inclination, RAAN, argument of periapsis)
        const xECI = x * (Math.cos(aopRad) * Math.cos(raanRad) - Math.sin(aopRad) * Math.sin(raanRad) * Math.cos(incRad))
                  - y * (Math.sin(aopRad) * Math.cos(raanRad) + Math.cos(aopRad) * Math.sin(raanRad) * Math.cos(incRad));

        const yECI = x * (Math.cos(aopRad) * Math.sin(raanRad) + Math.sin(aopRad) * Math.cos(raanRad) * Math.cos(incRad))
                  + y * (Math.cos(aopRad) * Math.cos(raanRad) * Math.cos(incRad) - Math.sin(aopRad) * Math.sin(raanRad));

        const zECI = x * Math.sin(aopRad) * Math.sin(incRad) + y * Math.cos(aopRad) * Math.sin(incRad);

        // Convert to Earth-fixed frame
        // This is a simplification - a real implementation would use Earth rotation
        const time = new Date();
        const gmst = Cesium.JulianDate.computeEarthRotationAngle(Cesium.JulianDate.fromDate(time));

        const cosGmst = Math.cos(gmst);
        const sinGmst = Math.sin(gmst);

        const x_ECEF = xECI * cosGmst + yECI * sinGmst;
        const y_ECEF = -xECI * sinGmst + yECI * cosGmst;
        const z_ECEF = zECI;

        return new Cesium.Cartesian3(x_ECEF, y_ECEF, z_ECEF);
    }

    // Create a position property for an orbit
    function createSampledPositionProperty(orbit, duration, interval) {
        const property = new Cesium.SampledPositionProperty();

        for (let i = 0; i <= duration; i += interval) {
            const position = computeOrbitalPosition(orbit, i);
            const time = Cesium.JulianDate.addSeconds(
                Cesium.JulianDate.now(),
                i,
                new Cesium.JulianDate()
            );
            property.addSample(time, position);
        }

        return property;
    }

    // Calculate optimal intercept between X-37B and a target
    function calculateOptimalIntercept(x37bOrbit, targetOrbit) {
        // This is a simplified calculation for demonstration
        // In reality, would use Lambert's problem solver for orbital transfers

        // Create a new orbit with parameters between the two
        const interceptOrbit = {
            semiMajorAxis: (x37bOrbit.semiMajorAxis + targetOrbit.semiMajorAxis) / 2,
            eccentricity: Math.max(0.001, Math.abs(x37bOrbit.eccentricity - targetOrbit.eccentricity) / 2),
            inclination: (x37bOrbit.inclination + targetOrbit.inclination) / 2,
            rightAscension: (x37bOrbit.rightAscension + (targetOrbit.rightAscension || 0)) / 2,
            argumentOfPeriapsis: (x37bOrbit.argumentOfPeriapsis + (targetOrbit.argumentOfPeriapsis || 0)) / 2
        };

        // Estimated time for transfer (very simplified)
        const mu = 3.986004418e14; // Earth's gravitational parameter (m^3/s^2)
        const transferTime = Math.PI * Math.sqrt(
            Math.pow((x37bOrbit.semiMajorAxis + targetOrbit.semiMajorAxis) * 500, 3) / mu
        );

        return {
            orbit: interceptOrbit,
            estimatedTime: transferTime
        };
    }

    // Create transfer orbit between two points
    function createTransferOrbit(startOrbit, endOrbit, duration) {
        // Calculate transfer orbit parameters (simplified Hohmann transfer)
        const startSMA = startOrbit.semiMajorAxis;
        const endSMA = endOrbit.semiMajorAxis;

        // Transfer orbit semi-major axis
        const transferSMA = (startSMA + endSMA) / 2;

        // Create transfer orbit
        const transferOrbit = {
            semiMajorAxis: transferSMA,
            eccentricity: Math.abs(startSMA - endSMA) / (startSMA + endSMA),
            inclination: (startOrbit.inclination + endOrbit.inclination) / 2,
            rightAscension: (startOrbit.rightAscension + (endOrbit.rightAscension || 0)) / 2,
            argumentOfPeriapsis: (startOrbit.argumentOfPeriapsis + (endOrbit.argumentOfPeriapsis || 0)) / 2
        };

        return transferOrbit;
    }

    // Update status display
    function updateStatusDisplay() {
        const statusElement = document.getElementById('x37b-mission-status');
        if (!statusElement) return;

        statusElement.textContent = missionStatus;

        // Update clock
        const clockElement = document.getElementById('x37b-mission-clock');
        if (clockElement) {
            const hours = Math.floor(missionClock / 3600);
            const minutes = Math.floor((missionClock % 3600) / 60);
            const seconds = Math.floor(missionClock % 60);

            clockElement.textContent =
                `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }

        // Update collected objects
        const collectedElement = document.getElementById('x37b-collected-objects');
        if (collectedElement) {
            collectedElement.textContent = collectedObjects.length;
        }
    }

    // Mission clock
    function startMissionClock() {
        missionClock = 0;
        clockInterval = setInterval(() => {
            missionClock++;
            updateStatusDisplay();
        }, 1000);
    }

    function stopMissionClock() {
        if (clockInterval) {
            clearInterval(clockInterval);
            clockInterval = null;
        }
    }

    // Public methods
    return {
        /**
         * Initialize the X-37B mission simulation
         * @param {Cesium.Viewer} cesiumViewer - The Cesium viewer
         */
        init: function(cesiumViewer) {
            viewer = cesiumViewer;

            // Create UI panel
            this.createUI();

            console.log('X-37B Mission Simulation initialized successfully');
        },

        /**
         * Create UI elements for the simulation
         */
        createUI: function() {
            // Create control panel
            const controlPanel = document.createElement('div');
            controlPanel.id = 'x37b-control-panel';
            controlPanel.className = 'control-panel';
            controlPanel.innerHTML = `
                <h3>X-37B Space Mission</h3>
                <div class="status-row">
                    <span>Status:</span>
                    <span id="x37b-mission-status" class="status-value">Idle</span>
                </div>
                <div class="status-row">
                    <span>Mission Clock:</span>
                    <span id="x37b-mission-clock" class="status-value">00:00:00</span>
                </div>
                <div class="status-row">
                    <span>Objects Collected:</span>
                    <span id="x37b-collected-objects" class="status-value">0</span>
                </div>
                <div class="button-container">
                    <button id="x37b-start-mission">Start Mission</button>
                    <button id="x37b-pause-mission" disabled>Pause</button>
                    <button id="x37b-reset-mission" disabled>Reset</button>
                </div>
            `;

            // Append to body or other container
            document.body.appendChild(controlPanel);

            // Add event listeners
            document.getElementById('x37b-start-mission').addEventListener('click', () => {
                if (!missionActive) {
                    this.startMission();
                    document.getElementById('x37b-start-mission').disabled = true;
                    document.getElementById('x37b-pause-mission').disabled = false;
                    document.getElementById('x37b-reset-mission').disabled = false;
                } else {
                    this.resumeMission();
                    document.getElementById('x37b-pause-mission').disabled = false;
                    document.getElementById('x37b-start-mission').textContent = 'Resume';
                }
            });

            document.getElementById('x37b-pause-mission').addEventListener('click', () => {
                this.pauseMission();
                document.getElementById('x37b-pause-mission').disabled = true;
                document.getElementById('x37b-start-mission').disabled = false;
                document.getElementById('x37b-start-mission').textContent = 'Resume';
            });

            document.getElementById('x37b-reset-mission').addEventListener('click', () => {
                this.resetMission();
                document.getElementById('x37b-start-mission').disabled = false;
                document.getElementById('x37b-start-mission').textContent = 'Start Mission';
                document.getElementById('x37b-pause-mission').disabled = true;
                document.getElementById('x37b-reset-mission').disabled = true;
            });

            // Add CSS
            const style = document.createElement('style');
            style.textContent = `
                .control-panel {
                    position: absolute;
                    top: 10px;
                    right: 10px;
                    background: rgba(0, 21, 64, 0.8);
                    color: white;
                    padding: 15px;
                    border-radius: 5px;
                    width: 250px;
                    box-shadow: 0 0 10px rgba(0, 0, 0, 0.5);
                    z-index: 1000;
                    font-family: Arial, sans-serif;
                }
                .control-panel h3 {
                    margin-top: 0;
                    color: #3498db;
                    border-bottom: 1px solid #3498db;
                    padding-bottom: 5px;
                }
                .status-row {
                    display: flex;
                    justify-content: space-between;
                    margin: 5px 0;
                }
                .status-value {
                    font-weight: bold;
                    font-family: monospace;
                }
                .button-container {
                    display: flex;
                    justify-content: space-between;
                    margin-top: 15px;
                }
                .button-container button {
                    background-color: #34495e;
                    color: white;
                    border: none;
                    padding: 5px 10px;
                    border-radius: 3px;
                    cursor: pointer;
                }
                .button-container button:hover {
                    background-color: #2c3e50;
                }
                .button-container button:disabled {
                    background-color: #7f8c8d;
                    cursor: not-allowed;
                }
            `;
            document.head.appendChild(style);
        },

        /**
         * Start the X-37B mission
         */
        startMission: function() {
            if (missionActive) return;

            // Clear previous mission if any
            this.resetMission();

            missionActive = true;
            missionStatus = 'Initializing';
            updateStatusDisplay();

            // Start mission clock
            startMissionClock();

            // Create ISS entity
            this.createISS();

            // Create space objects
            this.createSpaceObjects();

            // Create X-37B entity
            this.createX37B();

            // Plan mission
            this.planMission();

            console.log('X-37B mission started');
        },

        /**
         * Pause the mission
         */
        pauseMission: function() {
            if (!missionActive) return;

            stopMissionClock();
            missionStatus = 'Paused';
            updateStatusDisplay();

            console.log('X-37B mission paused');
        },

        /**
         * Resume the mission
         */
        resumeMission: function() {
            if (!missionActive) return;

            startMissionClock();
            missionStatus = 'In Progress';
            updateStatusDisplay();

            console.log('X-37B mission resumed');
        },

        /**
         * Reset the mission
         */
        resetMission: function() {
            stopMissionClock();
            missionClock = 0;
            missionActive = false;
            missionStatus = 'Idle';
            collectedObjects = [];
            currentTarget = null;
            deliveryInProgress = false;

            // Clear entities
            if (viewer) {
                viewer.entities.remove(x37bEntity);
                viewer.entities.remove(issEntity);
                viewer.entities.remove(orbitPath);

                spaceObjectEntities.forEach(entity => {
                    viewer.entities.remove(entity);
                });
                spaceObjectEntities = [];
            }

            updateStatusDisplay();
            console.log('X-37B mission reset');
        },

        /**
         * Create the ISS entity
         */
        createISS: function() {
            // Define ISS position based on orbital elements
            const issPositionProperty = createSampledPositionProperty(issOrbit, 24 * 3600, 60);

            // Create ISS entity
            issEntity = viewer.entities.add({
                name: 'International Space Station',
                position: issPositionProperty,
                model: {
                    uri: 'https://cesium.com/3d-models/iss.glb', // This URL is for illustration - use a real ISS model
                    minimumPixelSize: 128,
                    maximumScale: 20
                },
                path: {
                    resolution: 1,
                    material: new Cesium.PolylineGlowMaterialProperty({
                        glowPower: 0.2,
                        color: Cesium.Color.BLUE
                    }),
                    width: 2,
                    leadTime: 3600,
                    trailTime: 3600
                },
                label: {
                    text: 'ISS',
                    font: '14pt sans-serif',
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    outlineWidth: 2,
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    pixelOffset: new Cesium.Cartesian2(0, -20)
                }
            });

            missionStatus = 'ISS orbit established';
            updateStatusDisplay();
        },

        /**
         * Create space objects
         */
        createSpaceObjects: function() {
            spaceObjects.forEach(obj => {
                // Create orbit for the object
                const orbit = {
                    semiMajorAxis: obj.semiMajorAxis,
                    eccentricity: obj.eccentricity,
                    inclination: obj.inclination,
                    rightAscension: Math.random() * 360, // Random RAAN
                    argumentOfPeriapsis: Math.random() * 360 // Random argument of periapsis
                };

                // Create position property
                const positionProperty = createSampledPositionProperty(orbit, 24 * 3600, 60);

                // Create entity
                const entity = viewer.entities.add({
                    id: obj.id,
                    name: obj.name,
                    description: `
                        <h2>${obj.name}</h2>
                        <p><strong>Type:</strong> ${obj.type}</p>
                        <p><strong>Launch Date:</strong> ${obj.launchDate}</p>
                        <p><strong>Description:</strong> ${obj.description}</p>
                        <p><strong>Mass:</strong> ${obj.mass} kg</p>
                    `,
                    position: positionProperty,
                    orbit: orbit, // Store orbit parameters for later use
                    originalPosition: positionProperty.clone(), // Store original position
                    box: {
                        dimensions: new Cesium.Cartesian3(obj.size, obj.size, obj.size),
                        material: obj.color.withAlpha(0.8)
                    },
                    path: {
                        resolution: 1,
                        material: new Cesium.PolylineGlowMaterialProperty({
                            glowPower: 0.2,
                            color: obj.color.withAlpha(0.5)
                        }),
                        width: 1,
                        leadTime: 3600,
                        trailTime: 3600
                    },
                    label: {
                        text: obj.name,
                        font: '10pt sans-serif',
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        outlineWidth: 2,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                        pixelOffset: new Cesium.Cartesian2(0, -10),
                        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 5000000)
                    }
                });

                spaceObjectEntities.push(entity);
            });

            missionStatus = 'Space objects identified';
            updateStatusDisplay();
        },

        /**
         * Create the X-37B Space Plane entity
         */
        createX37B: function() {
            // Initial X-37B orbit (below ISS)
            const x37bInitialOrbit = {
                semiMajorAxis: 6700, // km
                eccentricity: 0.0002,
                inclination: 50.0, // degrees
                rightAscension: 180, // degrees
                argumentOfPeriapsis: 0 // degrees
            };

            // Create position property
            const positionProperty = createSampledPositionProperty(x37bInitialOrbit, 24 * 3600, 30);

            // Create entity
            x37bEntity = viewer.entities.add({
                name: 'X-37B Space Plane',
                position: positionProperty,
                orientation: new Cesium.CallbackProperty(function(time) {
                    // Calculate velocity to orient the model
                    const position1 = positionProperty.getValue(time);
                    const time2 = Cesium.JulianDate.addSeconds(time, 1, new Cesium.JulianDate());
                    const position2 = positionProperty.getValue(time2);

                    if (!position1 || !position2) {
                        return Cesium.Quaternion.IDENTITY;
                    }

                    const velocity = Cesium.Cartesian3.subtract(position2, position1, new Cesium.Cartesian3());
                    Cesium.Cartesian3.normalize(velocity, velocity);

                    const up = Cesium.Cartesian3.normalize(position1, new Cesium.Cartesian3());
                    const right = Cesium.Cartesian3.cross(velocity, up, new Cesium.Cartesian3());
                    Cesium.Cartesian3.normalize(right, right);

                    // Adjust for model facing
                    const matrix3 = new Cesium.Matrix3();
                    Cesium.Matrix3.setColumn(matrix3, 0, right, matrix3);
                    Cesium.Matrix3.setColumn(matrix3, 1, velocity, matrix3);
                    Cesium.Matrix3.setColumn(matrix3, 2, up, matrix3);

                    const quaternion = Cesium.Quaternion.fromRotationMatrix(matrix3);
                    return quaternion;
                }, false),
                model: {
                    uri: 'https://sandcastle.cesium.com/SampleData/models/CesiumAir.glb', // Placeholder
                    minimumPixelSize: 64,
                    maximumScale: 10,
                    color: Cesium.Color.WHITE
                },
                path: {
                    resolution: 1,
                    material: new Cesium.PolylineGlowMaterialProperty({
                        glowPower: 0.2,
                        color: Cesium.Color.GREEN
                    }),
                    width: 2,
                    leadTime: 1800,
                    trailTime: 1800
                },
                label: {
                    text: 'X-37B',
                    font: '14pt sans-serif',
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    outlineWidth: 2,
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    pixelOffset: new Cesium.Cartesian2(0, -20)
                }
            });

            // Store the orbit
            x37bEntity.orbit = x37bInitialOrbit;

            missionStatus = 'X-37B deployed';
            updateStatusDisplay();

            // Initial view
            viewer.zoomTo(x37bEntity);
        },

        /**
         * Plan the mission - collecting and delivering space objects
         */
        planMission: function() {
            if (!missionActive) return;

            missionStatus = 'Planning mission';
            updateStatusDisplay();

            // Sort objects by difficulty/priority
            // For this simulation, we'll use a simple approach: collect objects closest to X-37B's orbit first
            const sortedObjects = [...spaceObjectEntities].sort((a, b) => {
                const dIncA = Math.abs(a.orbit.inclination - x37bEntity.orbit.inclination);
                const dIncB = Math.abs(b.orbit.inclination - x37bEntity.orbit.inclination);
                return dIncA - dIncB;
            });

            // Start mission execution by targeting the first object
            setTimeout(() => {
                this.executeCollection(sortedObjects);
            }, 2000);
        },

        /**
         * Execute the collection mission
         * @param {Array} targetList - List of target objects to collect
         */
        executeCollection: function(targetList) {
            if (!missionActive || targetList.length === 0) {
                // If all objects collected, go to ISS
                if (missionActive && collectedObjects.length > 0) {
                    this.deliverToISS();
                }
                return;
            }

            // Get next target
            currentTarget = targetList.shift();

            missionStatus = `Targeting ${currentTarget.name}`;
            updateStatusDisplay();

            // Calculate intercept course
            const intercept = calculateOptimalIntercept(x37bEntity.orbit, currentTarget.orbit);

            // Create transfer orbit
            const transferOrbit = intercept.orbit;

            // Create new sampled positions for X-37B
            const transferTimeInSeconds = 3600; // 1 hour for demonstration
            const transferPositionProperty = createSampledPositionProperty(transferOrbit, transferTimeInSeconds, 30);

            // Update X-37B path
            x37bEntity.position = transferPositionProperty;
            x37bEntity.orbit = transferOrbit;

            // Zoom to the intercept
            viewer.zoomTo([x37bEntity, currentTarget]);

            // After transfer time, "collect" the object
            setTimeout(() => {
                this.collectObject(currentTarget, targetList);
            }, 10000); // 10 seconds for demonstration
        },

        /**
         * Collect a space object
         * @param {Cesium.Entity} object - The object to collect
         * @param {Array} remainingTargets - Remaining targets for recursive processing
         */
        collectObject: function(object, remainingTargets) {
            if (!missionActive) return;

            missionStatus = `Collecting ${object.name}`;
            updateStatusDisplay();

            // Hide the original object
            object.show = false;

            // Add to collected list
            collectedObjects.push(object);

            // Update display
            updateStatusDisplay();

            // Flash effect at collection point
            const collectPosition = object.position.getValue(Cesium.JulianDate.now());

            // Create flash effect
            const flashEntity = viewer.entities.add({
                position: collectPosition,
                ellipsoid: {
                    radii: new Cesium.Cartesian3(500, 500, 500),
                    material: new Cesium.TimeIntervalCollectionProperty()
                }
            });

            const start = Cesium.JulianDate.now();
            const end = Cesium.JulianDate.addSeconds(start, 2, new Cesium.JulianDate());

            flashEntity.ellipsoid.material.intervals.addInterval(
                new Cesium.TimeInterval({
                    start: start,
                    stop: end,
                    data: new Cesium.MaterialProperty({
                        getType: function() { return 'Color'; },
                        getValue: function(time) {
                            const t = Cesium.JulianDate.secondsDifference(time, start) / 2.0;
                            const alpha = Math.abs(Math.sin(t * Math.PI * 3));
                            return Cesium.Color.fromAlpha(Cesium.Color.YELLOW, alpha);
                        }
                    })
                })
            );

            // Remove flash after animation
            setTimeout(() => {
                viewer.entities.remove(flashEntity);

                // Move to next target or deliver to ISS
                if (remainingTargets.length > 0) {
                    this.executeCollection(remainingTargets);
                } else {
                    this.deliverToISS();
                }
            }, 2000);
        },

        /**
         * Deliver collected objects to the ISS
         */
        deliverToISS: function() {
            if (!missionActive || deliveryInProgress) return;

            deliveryInProgress = true;
            missionStatus = `Transferring to ISS (${collectedObjects.length} objects onboard)`;
            updateStatusDisplay();

            // Calculate transfer to ISS
            const transferOrbit = createTransferOrbit(x37bEntity.orbit, issOrbit, 3600);

            // Create new sampled positions for X-37B
            const transferTimeInSeconds = 3600; // 1 hour for demonstration
            const transferPositionProperty = createSampledPositionProperty(transferOrbit, transferTimeInSeconds, 30);

            // Update X-37B path
            x37bEntity.position = transferPositionProperty;
            x37bEntity.orbit = transferOrbit;

            // Zoom to the ISS
            viewer.zoomTo([x37bEntity, issEntity]);

            // After transfer time, deliver the objects
            setTimeout(() => {
                this.completeDelivery();
            }, 12000); // 12 seconds for demonstration
        },

        /**
         * Complete the delivery to the ISS
         */
        completeDelivery: function() {
            if (!missionActive) return;

            missionStatus = `Delivering ${collectedObjects.length} objects to ISS`;
            updateStatusDisplay();

            // Flash effect at ISS
            const issPosition = issEntity.position.getValue(Cesium.JulianDate.now());

            // Create flash effect
            const flashEntity = viewer.entities.add({
                position: issPosition,
                ellipsoid: {
                    radii: new Cesium.Cartesian3(1000, 1000, 1000),
                    material: new Cesium.TimeIntervalCollectionProperty()
                }
            });

            const start = Cesium.JulianDate.now();
            const end = Cesium.JulianDate.addSeconds(start, 3, new Cesium.JulianDate());

            flashEntity.ellipsoid.material.intervals.addInterval(
                new Cesium.TimeInterval({
                    start: start,
                    stop: end,
                    data: new Cesium.MaterialProperty({
                        getType: function() { return 'Color'; },
                        getValue: function(time) {
                            const t = Cesium.JulianDate.secondsDifference(time, start) / 3.0;
                            const alpha = Math.abs(Math.sin(t * Math.PI * 2));
                            return Cesium.Color.fromAlpha(Cesium.Color.BLUE, alpha);
                        }
                    })
                })
            );

            // Remove flash after animation
            setTimeout(() => {
                viewer.entities.remove(flashEntity);

                // Mission success!
                missionStatus = `Mission Complete! Delivered ${collectedObjects.length} objects to ISS`;
                updateStatusDisplay();

                // Reset delivery flag
                deliveryInProgress = false;

                // Stop mission clock
                stopMissionClock();

                // Update UI
                document.getElementById('x37b-start-mission').disabled = true;
                document.getElementById('x37b-pause-mission').disabled = true;

                // Show message
                const successMessage = viewer.entities.add({
                    position: issPosition,
                    label: {
                        text: 'MISSION ACCOMPLISHED',
                        font: '24pt sans-serif',
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        outlineWidth: 2,
                        verticalOrigin: Cesium.VerticalOrigin.CENTER,
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        pixelOffset: new Cesium.Cartesian2(0, -50),
                        fillColor: Cesium.Color.GREEN,
                        outlineColor: Cesium.Color.BLACK,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY
                    }
                });

                // Remove message after 10 seconds
                setTimeout(() => {
                    viewer.entities.remove(successMessage);
                }, 10000);
            }, 3000);
        }
    };
})();
