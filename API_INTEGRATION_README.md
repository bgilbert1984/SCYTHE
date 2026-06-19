# Enhanced K9 Signal Processor API Integration

This module extends the K9 Signal Processor with Shodan and Google Gemini API integration for more powerful RF signal analysis, device intelligence, and threat assessment capabilities.

## Overview

The Enhanced K9 Signal Processor combines the bio-inspired signal processing of the original K9 module with:

1. **Shodan Intelligence**: Identifies potential devices transmitting on detected frequencies, including device types, locations, and vulnerabilities.
2. **Google Gemini AI Analysis**: Provides advanced signal analysis, classification, and anomaly detection with natural language reasoning.
3. **Integrated Threat Assessment**: Combines signal characteristics, detected anomalies, and device intelligence to assess potential threat levels.

## Setup

### Prerequisites

1. Install required packages:
   ```bash
   pip install -r requirements_enhanced.txt
   ```

2. Set up API keys:
   Create a `.env.local` file in the project root with the following content:
   ```
   GOOGLE_GEMINI_API=your_gemini_api_key
   SHODAN_API_KEY=your_shodan_api_key
   ```

   Note: The Shodan API key needs to be added to your existing .env.local file.

### Usage

#### Basic usage:

```python
from enhanced_k9_processor import EnhancedK9SignalProcessor

# Initialize the processor
processor = EnhancedK9SignalProcessor(
    sensitivity=1.5,
    enable_shodan=True,
    enable_gemini=True
)

# Process a signal
result = processor.process_signal(freqs, amplitudes)

# Access enhanced data
gemini_analysis = result.get('gemini_analysis')
shodan_intel = result.get('shodan_intel')
threat_assessment = result.get('threat_assessment')
anomaly_detection = result.get('anomaly_detection')
```

#### Command-line demo:

```bash
python enhanced_k9_processor.py --save-plot --save-memory
```

Options:
- `--no-shodan`: Disable Shodan integration
- `--no-gemini`: Disable Gemini integration
- `--save-plot`: Save the demo plot to a file
- `--save-memory`: Save the signal memory to a file
- `--load-memory FILENAME`: Load signal memory from a file

## Integration Details

### 1. Shodan Integration (`ShodanRFIntegration`)

The Shodan integration module:
- Searches for devices transmitting on detected frequencies
- Maps frequencies to common protocols (WiFi, Bluetooth, LoRa, etc.)
- Extracts device types, countries, open ports, and vulnerabilities
- Caches results to minimize API usage

### 2. Gemini Integration (`GeminiRFAnalyzer`)

The Gemini integration module:
- Analyzes signal characteristics to identify modulation types
- Detects anomalies by comparing current signals to historical patterns
- Assesses potential threat levels based on signal + Shodan intelligence
- Provides detailed explanations and recommended actions
- Tracks token usage and implements caching for efficiency

### 3. Enhanced Signal Memory

The `EnhancedSignalMemory` class extends the original `SignalMemory` with:
- Shodan device intelligence
- Gemini analysis results
- Threat assessments
- Anomaly history

## Data Flow

1. Signal is processed by the original K9 bio-inspired algorithms
2. If significant, features are sent to Gemini for analysis and Shodan for device lookup
3. Results are combined for comprehensive intelligence
4. Anomaly detection compares current signal to historical patterns
5. Threat assessment evaluates all collected data
6. Results are stored in enhanced memory for future pattern recognition

## Security and Performance Considerations

- API keys are stored securely using environment variables
- Results are cached to minimize API calls and improve performance
- Rate limiting and error handling prevent API abuse
- Token usage tracking helps manage Gemini API costs
- Memory persistence allows storing and loading signal intelligence between sessions

## Recommended Usage

This enhanced system is ideal for:
- RF security monitoring
- Signal intelligence operations
- Anomaly detection in spectrum management
- Identifying potential security threats in wireless communications
- Building pattern memory of normal vs. abnormal RF activity
