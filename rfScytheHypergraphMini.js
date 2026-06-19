/**
 * RF SCYTHE Hypergraph Mini Integration
 * This file provides minimal integration between RF SCYTHE tactical demo and Hypergraph visualization
 */

// Create global namespace if not exists
if (typeof RF_SCYTHE === 'undefined') {
    window.RF_SCYTHE = {};
}

// Hypergraph integration module
RF_SCYTHE.Hypergraph = {
    // Initialize the RF Hypergraph visualization
    init: function(renderElement) {
        console.log('Initializing RF SCYTHE Hypergraph visualization');

        this.container = renderElement;
        this.nodes = new Map();
        this.hyperedges = new Map();
        this.options = {
            nodeSize: 1.0,
            nodeColor: 0x3a8ee6,
            edgeColor: 0x2080c0,
            selectedColor: 0xff0000,
            hyperedgeOpacity: 0.2
        };

        this.initScene();
        this.initControls();
        this.animate();

        return this;
    },

    // Initialize Three.js scene
    initScene: function() {
        // Create scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x111122);

        // Create camera
        this.camera = new THREE.PerspectiveCamera(
            60,
            this.container.clientWidth / this.container.clientHeight,
            0.1,
            1000
        );
        this.camera.position.set(0, 0, 30);

        // Create renderer
        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        this.container.appendChild(this.renderer.domElement);

        // Add lights
        const ambientLight = new THREE.AmbientLight(0x404040);
        this.scene.add(ambientLight);

        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.5);
        directionalLight.position.set(1, 1, 1);
        this.scene.add(directionalLight);

        // Create groups for nodes and edges
        this.nodesGroup = new THREE.Group();
        this.scene.add(this.nodesGroup);

        this.edgesGroup = new THREE.Group();
        this.scene.add(this.edgesGroup);

        this.hyperedgesGroup = new THREE.Group();
        this.scene.add(this.hyperedgesGroup);

        // Add grid for reference
        const gridHelper = new THREE.GridHelper(50, 50, 0x555555, 0x333333);
        gridHelper.position.y = -10;
        this.scene.add(gridHelper);

        // Handle window resize
        window.addEventListener('resize', () => {
            this.camera.aspect = this.container.clientWidth / this.container.clientHeight;
            this.camera.updateProjectionMatrix();
            this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        });
    },

    // Initialize controls
    initControls: function() {
        // Check for both possible OrbitControls locations
        const OrbitControlsClass = window.ThreeOrbitControls ||
                                 (window.THREE && window.THREE.OrbitControls);

        if (OrbitControlsClass) {
            this.controls = new OrbitControlsClass(this.camera, this.renderer.domElement);
            this.controls.enableDamping = true;
            this.controls.dampingFactor = 0.25;
            this.controls.screenSpacePanning = false;
            this.controls.maxDistance = 100;
        } else {
            console.warn('OrbitControls not available - camera controls disabled');
        }
    },

    // Animation loop
    animate: function() {
        // Don't animate if container is not visible or doesn't exist
        if (!this.container || this.container.offsetParent === null) {
            // Schedule next frame but don't render
            requestAnimationFrame(() => this.animate());
            return;
        }

        // Throttle to 30fps for performance
        const now = Date.now();
        if (this._lastRenderTime && now - this._lastRenderTime < 33) {
            requestAnimationFrame(() => this.animate());
            return;
        }
        this._lastRenderTime = now;

        // Update controls
        if (this.controls) {
            this.controls.update();
        }

        // Render scene
        this.renderer.render(this.scene, this.camera);

        // Schedule next frame
        requestAnimationFrame(() => this.animate());
    },

    // Add a signal node to the hypergraph
    addSignalNode: function(nodeData) {
        const { id, position, frequency, power, modulation } = nodeData;

        // Create node geometry
        const geometry = new THREE.SphereGeometry(this.options.nodeSize, 32, 32);

        // Determine color based on frequency band
        const normalizedFreq = (frequency - 300) / 2400; // Normalize to 0-1 range for typical RF
        const hue = Math.max(0, Math.min(0.8, normalizedFreq));
        const saturation = 0.8;
        const lightness = 0.5;

        // Create material with gradient based on power
        const material = new THREE.MeshStandardMaterial({
            color: new THREE.Color().setHSL(hue, saturation, lightness),
            emissive: new THREE.Color().setHSL(hue, saturation, lightness * 0.5),
            metalness: 0.3,
            roughness: 0.7
        });

        // Create mesh
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(position.x, position.y, position.z);

        // Add to scene
        this.nodesGroup.add(mesh);

        // Store node data
        mesh.userData.nodeData = nodeData;
        this.nodes.set(id, { data: nodeData, mesh });

        return mesh;
    },

    // Add an edge between nodes
    addEdge: function(edgeData) {
        const { id, sourceId, targetId, strength } = edgeData;

        // Get source and target nodes
        const sourceNode = this.nodes.get(sourceId);
        const targetNode = this.nodes.get(targetId);

        if (!sourceNode || !targetNode) {
            console.error(`Edge nodes not found: ${sourceId} -> ${targetId}`);
            return null;
        }

        // Create points for the line
        const points = [
            sourceNode.mesh.position.clone(),
            targetNode.mesh.position.clone()
        ];

        // Create the line geometry
        const geometry = new THREE.BufferGeometry().setFromPoints(points);

        // Create material based on strength
        const alpha = Math.min(1, Math.max(0.1, strength));
        const material = new THREE.LineBasicMaterial({
            color: this.options.edgeColor,
            transparent: true,
            opacity: alpha
        });

        // Create the line
        const line = new THREE.Line(geometry, material);

        // Add to scene
        this.edgesGroup.add(line);

        // Store edge data
        line.userData.edgeData = edgeData;
        if (!this.hyperedges.has(id)) {
            this.hyperedges.set(id, { data: edgeData, mesh: line });
        }

        return line;
    },

    // Add a hyperedge between 3+ nodes
    addHyperedge: function(edgeData) {
        const { id, nodeIds, strength, cardinality } = edgeData;

        // Get node positions
        const nodePositions = [];
        const nodes = [];

        for (const nodeId of nodeIds) {
            const node = this.nodes.get(nodeId);
            if (node) {
                nodePositions.push(node.mesh.position.clone());
                nodes.push(node);
            }
        }

        if (nodePositions.length < 3) {
            console.error(`Not enough nodes for hyperedge: ${id}`);

            // If we have at least 2 nodes, create a regular edge instead
            if (nodePositions.length === 2) {
                const simpleEdgeData = {
                    id: id,
                    sourceId: nodeIds[0],
                    targetId: nodeIds[1],
                    strength: strength
                };
                return this.addEdge(simpleEdgeData);
            }

            return null;
        }

        // Create lines for each node pair to show the hyperedge structure
        const lines = [];

        for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
                const points = [
                    nodes[i].mesh.position.clone(),
                    nodes[j].mesh.position.clone()
                ];

                const geometry = new THREE.BufferGeometry().setFromPoints(points);
                const lineMaterial = new THREE.LineBasicMaterial({
                    color: 0x2080c0,
                    transparent: true,
                    opacity: 0.2
                });

                const line = new THREE.Line(geometry, lineMaterial);
                this.hyperedgesGroup.add(line);
                lines.push(line);
            }
        }

        // Store hyperedge data
        this.hyperedges.set(id, {
            data: edgeData,
            meshes: lines
        });

        return lines;
    },

    // Load data from a JSON object
    loadData: function(data) {
        // Clear existing data
        this.clearAll();

        // Add nodes
        if (data.nodes && Array.isArray(data.nodes)) {
            for (const nodeData of data.nodes) {
                this.addSignalNode(nodeData);
            }
        }

        // Add edges
        if (data.edges && Array.isArray(data.edges)) {
            for (const edgeData of data.edges) {
                this.addEdge(edgeData);
            }
        }

        // Add hyperedges
        if (data.hyperedges && Array.isArray(data.hyperedges)) {
            for (const edgeData of data.hyperedges) {
                this.addHyperedge(edgeData);
            }
        }
    },

    // Clear all nodes and edges
    clearAll: function() {
        // Clear nodes
        this.nodes.forEach(node => {
            if (node.mesh && node.mesh.parent) {
                node.mesh.parent.remove(node.mesh);
            }
        });
        this.nodes.clear();

        // Clear edges
        this.hyperedges.forEach(edge => {
            if (edge.mesh && edge.mesh.parent) {
                edge.mesh.parent.remove(edge.mesh);
            }
            if (edge.meshes) {
                edge.meshes.forEach(mesh => {
                    if (mesh && mesh.parent) {
                        mesh.parent.remove(mesh);
                    }
                });
            }
        });
        this.hyperedges.clear();
    },

    // Generate some example data
    generateExampleData: function(nodeCount = 20, edgeCount = 30, hyperedgeCount = 10) {
        const data = {
            nodes: [],
            edges: [],
            hyperedges: []
        };

        // Generate nodes
        for (let i = 0; i < nodeCount; i++) {
            const position = {
                x: (Math.random() - 0.5) * 40,
                y: (Math.random() - 0.5) * 40,
                z: (Math.random() - 0.5) * 40
            };

            const frequency = 300 + Math.random() * 2400; // 300 MHz to 2.7 GHz
            const power = -120 + Math.random() * 80; // -120 dBm to -40 dBm

            const modulations = ['AM', 'FM', 'QPSK', 'QAM', 'OFDM', 'FHSS', 'DSSS'];
            const modulation = modulations[Math.floor(Math.random() * modulations.length)];

            data.nodes.push({
                id: `node_${i}`,
                position: position,
                frequency: frequency,
                power: power,
                modulation: modulation,
                metadata: {
                    detected: new Date().toISOString(),
                    bandwidth: 5 + Math.random() * 45, // 5-50 MHz
                    snr: 1 + Math.random() * 19 // 1-20 dB
                }
            });
        }

        // Generate edges (pairwise connections)
        for (let i = 0; i < edgeCount; i++) {
            const sourceIndex = Math.floor(Math.random() * nodeCount);
            let targetIndex;
            do {
                targetIndex = Math.floor(Math.random() * nodeCount);
            } while (targetIndex === sourceIndex);

            data.edges.push({
                id: `edge_${i}`,
                sourceId: `node_${sourceIndex}`,
                targetId: `node_${targetIndex}`,
                strength: Math.random(),
                metadata: {
                    coherence: Math.random(),
                    detected: new Date().toISOString()
                }
            });
        }

        // Generate hyperedges (3+ node connections)
        for (let i = 0; i < hyperedgeCount; i++) {
            // Random cardinality between 3 and 6
            const cardinality = Math.floor(Math.random() * 4) + 3;

            // Select random nodes
            const nodeIndices = new Set();
            while (nodeIndices.size < cardinality) {
                nodeIndices.add(Math.floor(Math.random() * nodeCount));
            }

            const nodeIds = Array.from(nodeIndices).map(index => `node_${index}`);

            data.hyperedges.push({
                id: `hyperedge_${i}`,
                nodeIds: nodeIds,
                strength: Math.random(),
                cardinality: cardinality,
                metadata: {
                    coherence: Math.random(),
                    detected: new Date().toISOString(),
                    analysisType: 'RF_HYPERGRAPH'
                }
            });
        }

        return data;
    }
};

// If RF_SCYTHE global exists, add this component to it
if (typeof window !== 'undefined' && window.RF_SCYTHE) {
    window.RF_SCYTHE.Hypergraph = RF_SCYTHE.Hypergraph;
}

console.log('RF SCYTHE Hypergraph Mini Integration loaded');
