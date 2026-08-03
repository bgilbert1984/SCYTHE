#include <cstdlib>
#include <iomanip>
#include <iostream>
#include "itm.h"

// Minimal stdin/stdout bridge for the official NTIA ITM_AREA_TLS function.
// Location and situation variability are held at 50 percent; time is explicit.
int main(int argc, char** argv) {
    if (argc != 14) {
        std::cerr
            << "usage: scythe-itm-area htx hrx txsite rxsite deltah climate "
               "n0 fmhz pol epsilon sigma mdvar time\n";
        return 64;
    }
    const double htx = std::atof(argv[1]);
    const double hrx = std::atof(argv[2]);
    const int txsite = std::atoi(argv[3]);
    const int rxsite = std::atoi(argv[4]);
    const double deltah = std::atof(argv[5]);
    const int climate = std::atoi(argv[6]);
    const double n0 = std::atof(argv[7]);
    const double fmhz = std::atof(argv[8]);
    const int pol = std::atoi(argv[9]);
    const double epsilon = std::atof(argv[10]);
    const double sigma = std::atof(argv[11]);
    const int mdvar = std::atoi(argv[12]);
    const double time = std::atof(argv[13]);

    std::cout << std::setprecision(15);
    double distance_km = 0;
    while (std::cin >> distance_km) {
        double loss_db = 0;
        long warnings = 0;
        const int status = ITM_AREA_TLS(
            htx, hrx, txsite, rxsite, distance_km, deltah, climate, n0,
            fmhz, pol, epsilon, sigma, mdvar, time, 50.0, 50.0,
            &loss_db, &warnings);
        std::cout << distance_km << ',' << loss_db << ',' << status << ','
                  << warnings << '\n';
    }
    return 0;
}
